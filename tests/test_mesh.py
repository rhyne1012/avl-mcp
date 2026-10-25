import csv
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from avl_mcp.geometry import inspect_geometry, sha256
from avl_mcp.mesh import (
    MeshSensitivityRequest,
    coefficient_deltas,
    refine_geometry,
    run_mesh_sensitivity,
)
from avl_mcp.models import AVLFailure, FlightCondition, References
from avl_mcp.runner import AVLRunner

FIXTURE = Path(__file__).parent / "fixtures" / "total.mrf"
CONDITION = FlightCondition(alpha_deg=3, mach=0.2)


@pytest.mark.parametrize(
    "values",
    [
        {"factors": [2, 3]},
        {"factors": [1]},
        {"factors": [1, 2, 2]},
        {"factors": [1, 3, 2]},
        {"factors": [1, True]},
        {"factors": [1, 2.0]},
        {"factors": [1, 101]},
        {"factors": list(range(1, 10))},
        {"metrics": []},
        {"metrics": ["Alpha"]},
        {"metrics": ["CLtot", "CLtot"]},
        {"absolute_tolerances": {"CLtot": -0.1}},
        {"absolute_tolerances": {"CLtot": float("nan")}},
        {"absolute_tolerances": {"CLtot": float("inf")}},
        {"metrics": ["CLtot"], "absolute_tolerances": {"Cmtot": 0.1}},
    ],
)
def test_request_rejects_invalid_refinements(values):
    with pytest.raises(ValidationError):
        MeshSensitivityRequest(**values)


def complex_geometry(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/foil with spaces.dat").write_text("foil\n1 0\n0 0\n1 0\n")
    (tmp_path / "body.dat").write_text("body\n0 0\n1 .1\n2 0\n")
    model = tmp_path / "mesh.avl"
    model.write_text("""SURFACE in the title is not a keyword
0.2
0 0 0
9 .9 10
.5 0 0
SURF
SECTION as a surface name is not a keyword
 2D0  1.0  3  -1.0 # surface-wide counts
YDUP
0
SECT
0 0 0 1 0 999 0 ! ignored section count
AFIL
'assets/foil with spaces.dat'
SECT
0 1 0 1 0 777 0 ! ignored final count
SURF
interval surface
 3 1.0 # only chord count
SECT
0 2 0 1 0 4 -1.5 ! first interval
AIRF
1 0
0 0
1 0
SECT
0 3 0 1 0 5 2.0 # second interval
NACA
0012
SECT
0 4 0 1 0 9 1.0 # ignored last interval
BODY
SURFACE body name
11 1.0 # stations are unchanged
BFIL
body.dat
""")
    return model


def test_refinement_preserves_inactive_counts_spacing_comments_airfoils_and_bodies(tmp_path):
    model = complex_geometry(tmp_path)
    original = model.read_bytes()
    obj = inspect_geometry(str(model))
    fine = refine_geometry(obj, 2)
    assert obj.panel_estimate == 39
    assert fine.panel_estimate == 156
    changed = [(a, b) for a, b in zip(obj.raw_lines, fine.raw_lines) if a != b]
    assert changed == [
        (" 2D0  1.0  3  -1.0 # surface-wide counts", " 4  1.0  6  -1.0 # surface-wide counts"),
        (" 3 1.0 # only chord count", " 6 1.0 # only chord count"),
        ("0 2 0 1 0 4 -1.5 ! first interval", "0 2 0 1 0 8 -1.5 ! first interval"),
        ("0 3 0 1 0 5 2.0 # second interval", "0 3 0 1 0 10 2.0 # second interval"),
    ]
    assert fine.bodies == obj.bodies
    assert fine.references == obj.references
    assert fine.dependencies == obj.dependencies
    assert fine.surfaces[0]["sections"][0]["n_span"] == 999
    assert fine.surfaces[1]["sections"][-1]["n_span"] == 9
    assert model.read_bytes() == original
    derived = tmp_path / "derived.avl"
    derived.write_text("\n".join(fine.raw_lines) + "\n")
    assert inspect_geometry(str(derived)).panel_estimate == 156


@pytest.mark.parametrize("factor", [0, -1, 1.5, True])
def test_refine_geometry_rejects_invalid_factor(vanilla, factor):
    with pytest.raises(AVLFailure, match="positive integer"):
        refine_geometry(inspect_geometry(str(vanilla)), factor)


def test_all_levels_preflight_before_any_execution(vanilla, tmp_path):
    runner = AVLRunner("/missing", str(tmp_path), max_vortices=1200)
    with pytest.raises(AVLFailure) as error:
        run_mesh_sensitivity(runner, str(vanilla), MeshSensitivityRequest())
    assert error.value.code == "MESH_LIMIT"
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    "options,code",
    [
        ({"timeout_seconds": 0}, "INVALID_TIMEOUT"),
        ({"length_unit": "meters"}, "INVALID_UNITS"),
        ({"case_name": "../escape"}, "INVALID_CASE_NAME"),
    ],
)
def test_runtime_invalid_options(vanilla, tmp_path, options, code):
    with pytest.raises(AVLFailure) as error:
        run_mesh_sensitivity(
            AVLRunner("/missing", str(tmp_path)), str(vanilla), MeshSensitivityRequest(), **options
        )
    assert error.value.code == code
    assert not (tmp_path / "runs").exists()


def test_unknown_control_preflight(vanilla, tmp_path):
    request = MeshSensitivityRequest(condition=FlightCondition(controls={"missing": 1}))
    with pytest.raises(AVLFailure) as error:
        run_mesh_sensitivity(AVLRunner("/missing", str(tmp_path)), str(vanilla), request)
    assert error.value.code == "UNKNOWN_CONTROL"


def test_delta_zero_denominator_and_signed_change():
    assert coefficient_deltas({"a": 0.0, "b": 0.5, "c": -0.3}, {"a": 0.0, "b": 0.0, "c": -0.2}) == {
        "a": {
            "signed": 0.0,
            "absolute": 0.0,
            "relative": None,
            "relative_status": "undefined_zero_reference",
        },
        "b": {
            "signed": 0.5,
            "absolute": 0.5,
            "relative": None,
            "relative_status": "undefined_zero_reference",
        },
        "c": {
            "signed": pytest.approx(-0.1),
            "absolute": pytest.approx(0.1),
            "relative": pytest.approx(0.5),
            "relative_status": "defined",
        },
    }


def fake_process(folder, commands, timeout):
    expected = json.loads((folder / "manifest.json").read_text())["estimated_vortices"]
    (folder / "total.mrf").write_text(
        FIXTURE.read_text().replace("   294      | # vortices", f"   {expected}      | # vortices")
    )
    for name, value in (
        ("commands.txt", commands),
        ("stdout.log", "fake test execution"),
        ("stderr.log", ""),
        ("process.json", "{}"),
    ):
        (folder / name).write_text(value)
    return {"version": "3.52", "test_fixture": True}


def test_relative_overflow_is_explicit_and_json_safe():
    deltas = coefficient_deltas({"CLtot": 1.0}, {"CLtot": 5e-324})
    assert deltas["CLtot"] == {
        "signed": 1.0,
        "absolute": 1.0,
        "relative": None,
        "relative_status": "undefined_overflow",
    }
    json.dumps(deltas, allow_nan=False)


def test_absolute_delta_overflow_retains_failure_reports(vanilla, tmp_path, monkeypatch):
    runner = AVLRunner("/missing", str(tmp_path))
    attempts = []

    def overflow_delta(folder, commands, timeout):
        process = fake_process(folder, commands, timeout)
        attempts.append(folder)
        total = folder / "total.mrf"
        total.write_text(
            total.read_text().replace(
                "6.754170403902354E-01      | CLtot",
                ("-1.0E308" if len(attempts) == 1 else "1.0E308") + "      | CLtot",
            )
        )
        return process

    monkeypatch.setattr(runner, "_process", overflow_delta)
    result = run_mesh_sensitivity(runner, str(vanilla), MeshSensitivityRequest(condition=CONDITION))
    assert not result["success"] and result["error"]["code"] == "NONFINITE_MESH_DELTA"
    assert result["completed_count"] == 1
    assert result["levels"][1]["status"] == "failed"
    assert Path(result["levels"][1]["artifacts"]["total.mrf"]).is_file()
    assert json.loads(Path(result["result_json"]).read_text()) == result
    json.dumps(result, allow_nan=False)


def test_native_count_mismatch_stops_without_coefficients_or_tolerance(
    vanilla, tmp_path, monkeypatch
):
    runner = AVLRunner("/missing", str(tmp_path))
    attempts = []

    def incorrect_count(folder, commands, timeout):
        process = fake_process(folder, commands, timeout)
        attempts.append(folder)
        if len(attempts) == 2:
            total = folder / "total.mrf"
            total.write_text(
                total.read_text().replace("1176      | # vortices", "900      | # vortices")
            )
        return process

    monkeypatch.setattr(runner, "_process", incorrect_count)
    result = run_mesh_sensitivity(
        runner,
        str(vanilla),
        MeshSensitivityRequest(condition=CONDITION, absolute_tolerances={"CLtot": 1}),
    )
    assert not result["success"] and result["error"]["code"] == "MESH_COUNT_MISMATCH"
    assert result["completed_count"] == 1 and result["attempted_count"] == 2
    failed = result["levels"][1]
    assert failed["counts"]["vortices"] == 900
    assert failed["error"]["expected_vortices"] == 1176
    assert "coefficients" not in failed and "adjacent_deltas" not in failed
    assert result["levels"][2]["status"] == "not_run"
    assert not result["last_adjacent_tolerance_check"]["evaluated"]
    assert Path(failed["artifacts"]["total.mrf"]).is_file()
    assert set(result["artifacts"]) == {"result.json", "summary.csv", "report.md", "manifest.json"}
    assert all(Path(path).is_file() for path in result["artifacts"].values())
    assert json.loads(Path(result["result_json"]).read_text()) == result


@pytest.mark.native
def test_native_array_clipping_is_rejected_with_raw_evidence(runner, tmp_path):
    model = tmp_path / "native-clip.avl"
    model.write_text("""Array clipping regression
0.2
0 0 0
1 1 1
0 0 0
SURFACE
Wing
20 1 2 1
SECTION
0 0 0 1 0
SECTION
0 1 0 1 0
""")
    result = run_mesh_sensitivity(
        runner,
        str(model),
        MeshSensitivityRequest(
            condition=CONDITION, factors=[1, 3], absolute_tolerances={"CLtot": 1}
        ),
    )
    assert not result["success"] and result["error"]["code"] == "MESH_COUNT_MISMATCH"
    assert result["completed_count"] == 1
    assert [level["estimated_vortices"] for level in result["levels"]] == [40, 360]
    assert [level["counts"]["vortices"] for level in result["levels"]] == [40, 300]
    failed = result["levels"][1]
    assert "Array overflow" in Path(failed["artifacts"]["stdout.log"]).read_text()
    assert "coefficients" not in failed and "baseline_deltas" not in failed
    assert not result["last_adjacent_tolerance_check"]["evaluated"]
    assert Path(failed["artifacts"]["total.mrf"]).is_file()
    assert "MESH_COUNT_MISMATCH" in Path(result["report_markdown"]).read_text()


def test_partial_failure_retains_completed_level_reports(vanilla, tmp_path, monkeypatch):
    runner = AVLRunner("/missing", str(tmp_path))
    attempts = []

    def fail_second(folder, commands, timeout):
        attempts.append(folder)
        if len(attempts) == 2:
            (folder / "stderr.log").write_text("intentional failure")
            raise AVLFailure("SOLVER_EXIT", "Intentional failure")
        return fake_process(folder, commands, timeout)

    monkeypatch.setattr(runner, "_process", fail_second)
    result = run_mesh_sensitivity(
        runner,
        str(vanilla),
        MeshSensitivityRequest(condition=CONDITION, absolute_tolerances={"CLtot": 1}),
    )
    assert not result["success"]
    assert result["source_preserved"]
    assert result["attempted_count"] == 2 and result["completed_count"] == 1
    assert [level["status"] for level in result["levels"]] == ["success", "failed", "not_run"]
    assert result["levels"][1]["error"]["code"] == "SOLVER_EXIT"
    assert not result["last_adjacent_tolerance_check"]["evaluated"]
    assert result["last_adjacent_tolerance_check"]["within_tolerance"] is None
    assert len(attempts) == 2
    with Path(result["summary_csv"]).open() as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["CLtot"]
    assert rows[1]["error_code"] == "SOLVER_EXIT"
    assert rows[2]["status"] == "not_run"
    assert "Stopped: SOLVER_EXIT" in Path(result["report_markdown"]).read_text()
    assert json.loads(Path(result["result_json"]).read_text()) == result
    assert all(
        Path(path).is_file()
        for level in result["levels"]
        for path in level.get("artifacts", {}).values()
    )


def test_total_deadline_is_shared_and_rechecked_after_process(vanilla, tmp_path, monkeypatch):
    runner = AVLRunner("/missing", str(tmp_path))
    clock = [100.0]
    timeouts = []
    monkeypatch.setattr("avl_mcp.mesh.time.monotonic", lambda: clock[0])

    def advance_clock(folder, commands, timeout):
        timeouts.append(timeout)
        clock[0] += 0.06
        return fake_process(folder, commands, timeout)

    monkeypatch.setattr(runner, "_process", advance_clock)
    result = run_mesh_sensitivity(
        runner, str(vanilla), MeshSensitivityRequest(condition=CONDITION), timeout_seconds=0.1
    )
    assert not result["success"]
    assert result["error"]["code"] == "TIMEOUT"
    assert result["completed_count"] == 1
    assert timeouts == pytest.approx([0.1, 0.04])


def test_source_changes_stop_before_accepting_native_result(vanilla, tmp_path, monkeypatch):
    source = tmp_path / "source"
    shutil.copytree(vanilla.parent, source)
    model = source / vanilla.name
    runner = AVLRunner("/missing", str(tmp_path / "work"))

    def change_source(folder, commands, timeout):
        result = fake_process(folder, commands, timeout)
        (source / "sd7037.dat").write_text("changed dependency")
        return result

    monkeypatch.setattr(runner, "_process", change_source)
    result = run_mesh_sensitivity(runner, str(model), MeshSensitivityRequest(condition=CONDITION))
    assert not result["success"]
    assert not result["source_preserved"]
    assert result["error"]["code"] == "SOURCE_CHANGED"
    assert result["completed_count"] == 0
    assert result["attempted_count"] == 1


def test_success_without_tolerances_does_not_assert_acceptance(vanilla, tmp_path, monkeypatch):
    runner = AVLRunner("/missing", str(tmp_path))
    monkeypatch.setattr(runner, "_process", fake_process)
    result = run_mesh_sensitivity(runner, str(vanilla), MeshSensitivityRequest(condition=CONDITION))
    assert result["success"]
    assert not result["last_adjacent_tolerance_check"]["evaluated"]
    assert result["last_adjacent_tolerance_check"]["within_tolerance"] is None
    assert "No mesh convergence" in Path(result["report_markdown"]).read_text()


@pytest.mark.native
def test_native_vanilla_sensitivity_and_explicit_thresholds(runner, vanilla):
    source_hashes = {str(p): sha256(p) for p in vanilla.parent.iterdir() if p.is_file()}
    result = run_mesh_sensitivity(
        runner,
        str(vanilla),
        MeshSensitivityRequest(
            condition=CONDITION, absolute_tolerances={"CLtot": 0.01, "Cmtot": 0.01}
        ),
        length_unit="m",
    )
    assert result["success"], result
    assert [level["counts"]["vortices"] for level in result["levels"]] == [294, 1176, 2646]
    assert [level["coefficients"]["Cmtot"] for level in result["levels"]] == pytest.approx(
        [0.119915598, 0.09333507, 0.07790373], abs=1e-7
    )
    final = result["levels"][-1]
    assert final["baseline_deltas"]["Cmtot"]["signed"] < -0.04
    assert final["adjacent_deltas"]["Cmtot"]["absolute"] > 0.01
    check = result["last_adjacent_tolerance_check"]
    assert check["evaluated"] and not check["within_tolerance"]
    assert check["metrics"]["CLtot"]["within_tolerance"]
    assert not check["metrics"]["Cmtot"]["within_tolerance"]
    assert all(sha256(Path(path)) == digest for path, digest in source_hashes.items())
    assert result["source_preserved"]
    assert all(
        Path(path).is_file() for level in result["levels"] for path in level["artifacts"].values()
    )
    assert json.loads(Path(result["result_json"]).read_text()) == result


@pytest.mark.native
def test_native_input_root_reference_and_control_preserved(runner, vanilla, tmp_path):
    restricted = AVLRunner(str(runner.executable), str(tmp_path / "work"), str(vanilla.parent))
    references = References(sref=10, cref=1, bref=11, xref=0.6, yref=0.1, zref=0.03)
    condition = FlightCondition(alpha_deg=2, mach=0.1, controls={"elevator": 2})
    result = run_mesh_sensitivity(
        restricted,
        str(vanilla),
        MeshSensitivityRequest(condition=condition, factors=[1, 2]),
        references=references,
        length_unit="ft",
    )
    assert result["success"], result
    assert result["references"] == references.model_dump()
    for level in result["levels"]:
        raw = json.loads(Path(level["artifacts"]["result.json"]).read_text())
        fields = raw["total"]["fields"]
        assert fields["Alpha"] == 2 and fields["Mach"] == 0.1
        assert fields["Sref"] == 10 and fields["Xref"] == 0.6 and fields["Zref"] == 0.03
        assert raw["total"]["controls"]["elevator"] == 2
        assert raw["geometry_length_unit"] == "ft"
        assert raw["solver"]["display_unset"]
    with pytest.raises(AVLFailure) as error:
        run_mesh_sensitivity(
            restricted, result["levels"][0]["artifacts"]["model.avl"], MeshSensitivityRequest()
        )
    assert error.value.code == "PATH_OUTSIDE_ROOT"


@pytest.mark.native
def test_native_section_discretization_and_body_evidence(runner, bd):
    original = inspect_geometry(str(bd))
    source_hashes = {str(bd): sha256(bd)} | {d["path"]: d["sha256"] for d in original.dependencies}
    result = run_mesh_sensitivity(
        runner,
        str(bd),
        MeshSensitivityRequest(condition=CONDITION, factors=[1, 2]),
        length_unit="in",
    )
    assert result["success"], result
    assert result["body_discretization"] == [{"name": "Fuse pod", "n_body": 12, "refined": False}]
    assert [level["counts"]["vortices"] for level in result["levels"]] == [484, 1936]
    fine = inspect_geometry(result["levels"][1]["artifacts"]["model.avl"])
    assert fine.bodies[0]["n_body"] == 12
    assert fine.surfaces[1]["sections"][0]["n_span"] == 14
    assert fine.surfaces[2]["sections"][1]["n_span"] == 12
    assert all(sha256(Path(path)) == digest for path, digest in source_hashes.items())
