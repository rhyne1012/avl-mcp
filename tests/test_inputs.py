import shutil

import pytest
from pydantic import ValidationError

from avl_mcp.geometry import inspect_geometry
from avl_mcp.models import AVLFailure, FlightCondition, References
from avl_mcp.runner import AVLRunner


def editable(vanilla, tmp_path, replacement):
    shutil.copytree(vanilla.parent, tmp_path / "model")
    p = tmp_path / "model/vanilla.avl"
    p.write_text(replacement(p.read_text()))
    return str(p)


def test_official_geometries(vanilla, bd):
    assert inspect_geometry(str(vanilla)).controls == ["flap", "aileron", "elevator", "rudder"]
    b = inspect_geometry(str(bd))
    assert b.bodies[0]["body_file"] == "fuseBD.dat"
    assert len(b.dependencies) == 7
    assert b.references["sref"] == 1000


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda t: t.replace("sd7037.dat", "missing.dat"), "MISSING_DEPENDENCY"),
        (lambda t: t.replace("sd7037.dat", "../outside.dat"), "UNSAFE_DEPENDENCY"),
        (lambda t: t.replace("sd7037.dat", "/etc/passwd"), "UNSAFE_DEPENDENCY"),
        (lambda t: t.replace("1.0     0.0   0", "0.0     0.0   0", 1), "INVALID_GEOMETRY"),
        (lambda t: t.replace("9.0     0.9", "nan     0.9"), "INVALID_GEOMETRY"),
        (lambda t: t.replace("9.0     0.9", "0.0     0.9"), "INVALID_GEOMETRY"),
        (lambda t: t.replace("TRANSLATE", "UNKNOWN"), "UNSUPPORTED_KEYWORD"),
        (lambda t: "truncated\n0\n0 0 0\n", "INVALID_GEOMETRY"),
    ],
)
def test_reject_bad_geometry(vanilla, tmp_path, change, code):
    with pytest.raises(AVLFailure) as err:
        inspect_geometry(editable(vanilla, tmp_path, change))
    assert err.value.code == code


def test_symlink_dependency_escape(vanilla, tmp_path):
    p = editable(vanilla, tmp_path, lambda t: t)
    foil = tmp_path / "model/sd7037.dat"
    foil.unlink()
    outside = tmp_path / "foil.dat"
    outside.write_text("outside")
    foil.symlink_to(outside)
    with pytest.raises(AVLFailure, match="within model directory"):
        inspect_geometry(p)


def test_allowed_root(vanilla, tmp_path):
    with pytest.raises(AVLFailure) as err:
        inspect_geometry(str(vanilla), tmp_path)
    assert err.value.code == "PATH_OUTSIDE_ROOT"


@pytest.mark.parametrize(
    "values",
    [
        {"alpha_deg": float("nan")},
        {"mach": 1.2},
        {"controls": {"elevator": float("inf")}},
        {"pb_2v": 0.2},
        {"unknown": 3},
    ],
)
def test_flight_constraints(values):
    with pytest.raises(ValidationError):
        FlightCondition(**values)


def test_bad_reference():
    with pytest.raises(ValidationError):
        References(sref=0, cref=1, bref=1, xref=0, yref=0, zref=0)


def test_unknown_controls_fail_before_execution(vanilla, tmp_path):
    runner = AVLRunner("/not-a-binary", str(tmp_path))
    with pytest.raises(AVLFailure) as err:
        runner.run(str(vanilla), FlightCondition(controls={"not_elevator": 2}))
    assert err.value.code == "UNKNOWN_CONTROL"
    assert not (tmp_path / "runs").exists()


def test_mesh_limit(vanilla, tmp_path):
    with pytest.raises(AVLFailure) as err:
        AVLRunner("/not-a-binary", str(tmp_path), max_vortices=20).validate(str(vanilla))
    assert err.value.code == "MESH_LIMIT"


def test_case_name_injection(vanilla, tmp_path):
    with pytest.raises(AVLFailure) as err:
        AVLRunner("/not-a-binary", str(tmp_path)).run(str(vanilla), case_name="../escape")
    assert err.value.code == "INVALID_CASE_NAME"
