"""Dimensional trim must preserve units, CG references and independent verification."""

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from avl_mcp import physical_trim
from avl_mcp.geometry import sha256
from avl_mcp.jobs import JobManager
from avl_mcp.models import AVLFailure, References, TrimRequest
from avl_mcp.physical_trim import PhysicalTrimRequest, prepare_trim, run_physical_trim
from avl_mcp.runner import AVLRunner


def physical_request(**changes):
    return PhysicalTrimRequest(
        **(
            {
                "weight_n": 2976.75,
                "density_kg_m3": 1.225,
                "speed_m_s": 30,
                "geometry_length_unit": "m",
                "cg": {"x": 0.6, "y": 0, "z": 0.03, "length_unit": "m"},
                "control": "elevator",
                "mach": 0.2,
                "alpha_bounds_deg": (-10, 15),
                "control_bounds": (-30, 30),
            }
            | changes
        )
    )


@pytest.mark.parametrize(
    "change",
    [
        {"weight_n": None},
        {"mass_kg": 100},
        {"weight_n": 0},
        {"weight_n": -1},
        {"weight_n": float("inf")},
        {"weight_n": float("nan")},
        {"weight_n": None, "mass_kg": -1},
        {"weight_n": None, "mass_kg": float("nan")},
        {"gravity_m_s2": 0},
        {"gravity_m_s2": float("inf")},
        {"density_kg_m3": 0},
        {"density_kg_m3": float("nan")},
        {"speed_m_s": -1},
        {"speed_m_s": float("inf")},
        {"geometry_length_unit": "unspecified"},
        {"cg": {"x": 0.6, "y": 0, "z": 0.03}},
        {"cg": {"x": 0.6, "y": 0, "z": 0.03, "length_unit": "unspecified"}},
        {"cg": {"x": float("inf"), "y": 0, "z": 0.03, "length_unit": "m"}},
        {"cg": {"x": 0.6, "y": 0, "z": 0.03, "length_unit": "m", "frame": "body"}},
        {"mach": 0.7},
        {"alpha_bounds_deg": (3, -3)},
        {"alpha_bounds_deg": (-31, 1)},
        {"control_bounds": (0, 0)},
        {"control_bounds": (-181, 180)},
        {"fixed_controls": {"flap": float("nan")}},
        {"fixed_controls": {"flap": 181}},
        {"target_cl": 0.6},
        {"target_cm": 0.01},
        {"beta_deg": 1},
    ],
)
def test_rejects_ambiguous_loading_units_and_unsupported_targets(change):
    with pytest.raises(ValidationError):
        physical_request(**change)


@pytest.mark.parametrize(
    "name",
    [
        "density_kg_m3",
        "speed_m_s",
        "geometry_length_unit",
        "cg",
        "mach",
        "control",
        "alpha_bounds_deg",
        "control_bounds",
    ],
)
def test_physical_operating_assumptions_require_explicit_inputs(name):
    values = physical_request().model_dump()
    del values[name]
    with pytest.raises(ValidationError):
        PhysicalTrimRequest(**values)


def test_preparation_is_read_only_and_usable_by_existing_trim(tmp_path, vanilla):
    engine = AVLRunner("/no-native-binary", str(tmp_path))
    before = {p: sha256(p) for p in vanilla.parent.iterdir() if p.is_file()}
    result = prepare_trim(engine, str(vanilla), physical_request(fixed_controls={"flap": 2}))
    assert result["success"] and not result["solver_executed"]
    target = TrimRequest(**result["trim_request"])
    refs = References(**result["references"])
    assert target.target_cl == pytest.approx(0.6)
    assert target.target_cm == 0
    assert target.mach == 0.2
    assert target.fixed_controls == {"flap": 2}
    assert refs == References(sref=9, cref=0.9, bref=10, xref=0.6, yref=0, zref=0.03)
    provenance = result["physical_trim"]
    assert provenance["reference_source"] == "geometry_header"
    assert provenance["references_before_cg"]["xref"] == 0.5
    assert provenance["references"] == result["references"]
    assert provenance["source_inputs"] == {
        str(p): checksum for p, checksum in before.items() if p.suffix in (".avl", ".dat")
    }
    conversion = provenance["conversion"]
    assert conversion["weight_source"] == "weight_n"
    assert conversion["gravity_applied"] is False
    assert conversion["dynamic_pressure_pa"] == pytest.approx(551.25)
    assert conversion["q_sref_n"] == pytest.approx(4961.25)
    assert not (tmp_path / "runs").exists()
    assert all(sha256(path) == checksum for path, checksum in before.items())


def test_mass_uses_declared_gravity_but_weight_does_not(tmp_path, vanilla):
    engine = AVLRunner("/no-native-binary", str(tmp_path))
    mass = prepare_trim(
        engine,
        str(vanilla),
        physical_request(
            weight_n=None,
            mass_kg=300,
            gravity_m_s2=10,
        ),
    )
    weight = prepare_trim(engine, str(vanilla), physical_request(weight_n=3000, gravity_m_s2=1))
    assert mass["trim_request"] == weight["trim_request"]
    conversion = mass["physical_trim"]["conversion"]
    assert conversion["weight_n"] == 3000
    assert conversion["weight_source"] == "mass_kg_times_gravity"
    assert conversion["gravity_applied"] is True
    assert weight["physical_trim"]["conversion"]["gravity_applied"] is False


@pytest.mark.parametrize("unit,scale", [("m", 1), ("ft", 0.3048), ("in", 0.0254)])
def test_reference_area_and_cg_conversion_preserve_physical_dimensions(
    tmp_path, vanilla, unit, scale
):
    engine = AVLRunner("/no-native-binary", str(tmp_path))
    references = References(
        sref=9 / scale**2, cref=0.9 / scale, bref=10 / scale, xref=99, yref=98, zref=97
    )
    result = prepare_trim(
        engine, str(vanilla), physical_request(geometry_length_unit=unit), references
    )
    conversion = result["physical_trim"]["conversion"]
    assert result["trim_request"]["target_cl"] == pytest.approx(0.6)
    assert conversion["reference_area_m2"] == pytest.approx(9)
    assert conversion["reference_chord_m"] == pytest.approx(0.9)
    assert conversion["reference_span_m"] == pytest.approx(10)
    assert conversion["cg_m"] == pytest.approx({"x": 0.6, "y": 0, "z": 0.03})
    assert conversion["cg_native"] == pytest.approx({"x": 0.6 / scale, "y": 0, "z": 0.03 / scale})
    assert result["references"]["sref"] == references.sref
    assert result["references"]["xref"] == pytest.approx(0.6 / scale)
    assert result["physical_trim"]["reference_source"] == "references_override"
    assert result["physical_trim"]["references_before_cg"] == references.model_dump()
    assert result["physical_trim"]["geometry_header_references"]["sref"] == 9
    assert references.xref == 99


def test_cg_conversion_is_independent_from_geometry_units(tmp_path, vanilla):
    engine = AVLRunner("/no-native-binary", str(tmp_path))
    result = prepare_trim(
        engine,
        str(vanilla),
        physical_request(
            geometry_length_unit="ft",
            cg={"x": 24, "y": -1, "z": 2, "length_unit": "in"},
        ),
    )
    assert result["references"]["xref"] == pytest.approx(2)
    assert result["references"]["yref"] == pytest.approx(-1 / 12)
    assert result["references"]["zref"] == pytest.approx(1 / 6)
    assert result["physical_trim"]["conversion"]["reference_area_m2"] == pytest.approx(
        9 * 0.3048**2
    )


@pytest.mark.parametrize(
    "change",
    [
        {"weight_n": None, "mass_kg": 1e308, "gravity_m_s2": 10},
        {"speed_m_s": 1e308},
        {"speed_m_s": 1e-300},
        {"weight_n": 1e308, "speed_m_s": 1e-100},
        {"weight_n": 1e-300, "speed_m_s": 1e100},
        {"geometry_length_unit": "in", "cg": {"x": 1e308, "y": 0, "z": 0, "length_unit": "m"}},
    ],
)
def test_derived_overflow_and_underflow_fail_before_native_execution(tmp_path, vanilla, change):
    engine = AVLRunner("/no-native-binary", str(tmp_path))
    with pytest.raises(AVLFailure, match="not representable") as exc:
        run_physical_trim(engine, str(vanilla), physical_request(**change))
    assert exc.value.code == "PHYSICAL_INPUT_RANGE"
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    "change,code",
    [
        ({"control": "unknown"}, "UNKNOWN_CONTROL"),
        ({"fixed_controls": {"unknown": 1}}, "UNKNOWN_CONTROL"),
        ({"fixed_controls": {"elevator": 1}}, "TRIM_CONTROL_CONFLICT"),
    ],
)
def test_prepared_inputs_obey_existing_control_contract(tmp_path, vanilla, change, code):
    engine = AVLRunner("/no-native-binary", str(tmp_path))
    with pytest.raises(AVLFailure) as exc:
        prepare_trim(engine, str(vanilla), physical_request(**change))
    assert exc.value.code == code
    assert not (tmp_path / "runs").exists()


def test_numerical_failure_retains_dimensional_provenance(tmp_path, vanilla, monkeypatch):
    engine = AVLRunner("/no-native-binary", str(tmp_path))

    def fail(directory, commands, timeout):
        assert json.loads((directory / "physical-trim.json").read_text())["input"]["weight_n"] > 0
        raise AVLFailure("SOLVER_REPORTED_ERROR", "cannot trim")

    monkeypatch.setattr(engine, "_process", fail)
    result = run_physical_trim(engine, str(vanilla), physical_request(), outputs=["total"])
    assert not result["success"]
    assert result["error"]["code"] == "SOLVER_REPORTED_ERROR"
    assert not result["trim"]["accepted"]
    assert result["physical_trim"]["conversion"]["target_cl"] == pytest.approx(0.6)
    folder = Path(result["run_directory"])
    assert json.loads((folder / "result.json").read_text()) == result
    assert (
        json.loads((folder / "manifest.json").read_text())["physical_trim"]
        == result["physical_trim"]
    )
    assert json.loads((folder / "physical-trim.json").read_text()) == result["physical_trim"]
    assert all(Path(path).is_file() for path in result["artifacts"].values())


@pytest.mark.parametrize("changed_file", ["vanilla.avl", "sd7037.dat"])
def test_changed_input_between_conversion_and_staging_cannot_use_stale_cl(
    tmp_path,
    vanilla,
    monkeypatch,
    changed_file,
):
    model_folder = tmp_path / "source"
    shutil.copytree(vanilla.parent, model_folder)
    model = model_folder / vanilla.name
    engine = AVLRunner("/no-native-binary", str(tmp_path / "jobs"))
    original_run_trim = physical_trim.run_trim

    def change_then_run(*args, **kwargs):
        path = model_folder / changed_file
        path.write_text(path.read_text() + "\n# changed after CL derivation\n")
        return original_run_trim(*args, **kwargs)

    monkeypatch.setattr(physical_trim, "run_trim", change_then_run)
    result = run_physical_trim(engine, str(model), physical_request(), outputs=["total"])
    assert not result["success"]
    assert result["error"]["code"] == "SOURCE_CHANGED"
    assert not result["trim"]["native_converged"]
    assert result["physical_trim"]["source_inputs"][str(model_folder / changed_file)] != sha256(
        model_folder / changed_file
    )
    folder = Path(result["run_directory"])
    assert (
        json.loads((folder / "manifest.json").read_text())["physical_trim"]
        == result["physical_trim"]
    )
    assert not (folder / "commands.txt").exists()


@pytest.mark.native
def test_physical_trim_native_matches_prepared_coefficient_trim_and_replays(runner, vanilla):
    request = physical_request(weight_n=None, mass_kg=2976.75 / 9.80665, fixed_controls={"flap": 2})
    before = {p: sha256(p) for p in vanilla.parent.iterdir() if p.is_file()}
    prepared = prepare_trim(runner, str(vanilla), request)
    result = run_physical_trim(runner, str(vanilla), request, outputs=["total"])
    assert result["success"], result
    assert result["analysis_type"] == "longitudinal_trim"
    assert result["trim"]["accepted"]
    assert result["trim"]["verification"]["status"] == "passed"
    assert result["physical_trim"] == prepared["physical_trim"]
    fields = result["total"]["fields"]
    assert fields["CLtot"] * prepared["physical_trim"]["conversion"]["q_sref_n"] == pytest.approx(
        2976.75,
        abs=1e-3,
    )
    assert fields["Cmtot"] == pytest.approx(0, abs=1e-6)
    assert [fields[key] for key in ("Xref", "Yref", "Zref")] == [0.6, 0, 0.03]
    assert result["condition"]["controls"]["flap"] == 2
    ordinary = runner.trim(
        str(vanilla),
        TrimRequest(**prepared["trim_request"]),
        references=References(**prepared["references"]),
        length_unit="m",
        outputs=["total"],
    )
    assert ordinary["success"], ordinary
    assert result["total"] == ordinary["total"]
    replay = Path(result["trim"]["verification"]["run_directory"])
    assert (replay / "commands.txt").is_file()
    assert replay != Path(result["run_directory"])
    assert all(sha256(path) == checksum for path, checksum in before.items())
    default_page = JobManager(runner).results(result["job_id"])
    assert default_page["rows"][0]["physical_trim"] == result["physical_trim"]
    queried = JobManager(runner).results(
        result["job_id"],
        fields=[
            "physical_trim.conversion.target_cl",
            "physical_trim.conversion.cg_native.x",
        ],
    )
    assert queried["rows"][0]["fields"] == pytest.approx(
        {
            "physical_trim.conversion.target_cl": 0.6,
            "physical_trim.conversion.cg_native.x": 0.6,
        }
    )


@pytest.mark.native
def test_physical_trim_native_preserves_override_dimensions_and_converts_cg(runner, vanilla):
    references = References(sref=10, cref=1, bref=11, xref=100, yref=100, zref=100)
    request = physical_request(
        weight_n=0.6 * 0.5 * 1.225 * 30**2 * 10,
        cg={"x": 0.6 / 0.0254, "y": 0, "z": 0.03 / 0.0254, "length_unit": "in"},
    )
    result = run_physical_trim(runner, str(vanilla), request, references, outputs=["total"])
    assert result["success"], result
    assert result["trim"]["verification"]["status"] == "passed"
    fields = result["total"]["fields"]
    assert [fields[key] for key in ("Sref", "Cref", "Bref")] == [10, 1, 11]
    assert [fields[key] for key in ("Xref", "Yref", "Zref")] == [0.6, 0, 0.03]
    assert result["physical_trim"]["reference_source"] == "references_override"
    assert result["physical_trim"]["references_before_cg"] == references.model_dump()
    assert references.xref == 100


@pytest.mark.native
def test_rejected_native_bounds_keep_dimensional_inputs_and_raw_evidence(runner, vanilla):
    result = run_physical_trim(
        runner,
        str(vanilla),
        physical_request(alpha_bounds_deg=(-1, 0)),
        outputs=["total"],
    )
    assert not result["success"]
    assert result["error"]["code"] == "TRIM_OUT_OF_BOUNDS"
    assert result["trim"]["native_converged"]
    assert result["physical_trim"]["conversion"]["target_cl"] == pytest.approx(0.6)
    folder = Path(result["run_directory"])
    assert (folder / "total.mrf").is_file()
    assert (
        json.loads((folder / "manifest.json").read_text())["physical_trim"]
        == result["physical_trim"]
    )
