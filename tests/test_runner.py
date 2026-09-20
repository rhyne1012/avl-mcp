import json
import math
import sys
import time
from pathlib import Path

import pytest

from avl_mcp.geometry import sha256
from avl_mcp.models import FlightCondition, References
from avl_mcp.runner import AVLRunner


@pytest.mark.native
@pytest.mark.parametrize("fixture_name", ["vanilla", "bd"])
def test_official_headless_case(runner, fixture_name, request):
    model = request.getfixturevalue(fixture_name)
    before = {p: sha256(p) for p in model.parent.iterdir() if p.is_file()}
    result = runner.run(str(model), FlightCondition(alpha_deg=3, mach=0.2), case_name=fixture_name)
    assert result["success"], result
    assert result["solver"]["display_unset"]
    assert result["total"]["fields"]["Alpha"] == 3
    assert result["total"]["controls"] == {k: 0 for k in result["total"]["controls"]}
    assert result["strip_count"] > 0
    assert result["source_preserved"]
    assert all(sha256(p) == h for p, h in before.items())
    assert all(Path(p).is_file() for p in result["artifacts"].values())


@pytest.mark.native
def test_reference_override_and_stale_run_ignored(runner, vanilla):
    ref = References(sref=10, cref=1, bref=11, xref=0.6, yref=0, zref=0.03)
    out = runner.run(str(vanilla), FlightCondition(alpha_deg=2, mach=0.1), ref, "references")
    assert out["success"], out
    actual = out["total"]["fields"]
    assert actual["Sref"] == 10 and actual["Xref"] == 0.6 and actual["Zref"] == 0.03
    assert actual["Alpha"] == 2 and actual["Mach"] == 0.1


@pytest.mark.native
def test_derivatives_by_independent_perturbations(runner, vanilla):
    base = runner.run(str(vanilla), FlightCondition(alpha_deg=3, mach=0.2), case_name="deriv-base")
    assert base["success"], base
    for field, step, coeff, table, key, factor in [
        ("alpha_deg", 0.01, "CLtot", "stability", "CLa", math.pi / 180),
        ("alpha_deg", 0.01, "Cmtot", "stability", "Cma", math.pi / 180),
        ("beta_deg", 0.01, "CYtot", "stability", "CYb", math.pi / 180),
        ("qc_2v", 0.0001, "Cmtot", "body", "Cmq", 1),
        ("pb_2v", 0.0001, "Cltot", "body", "Clp", 1),
        ("rb_2v", 0.0001, "Cntot", "body", "Cnr", 1),
    ]:
        vals = []
        for sign in (-1, 1):
            c = dict(alpha_deg=3, mach=0.2)
            c[field] = c.get(field, 0) + sign * step
            result = runner.run(str(vanilla), FlightCondition(**c), case_name="fd")
            assert result["success"], result
            vals.append(result["total"]["fields"][coeff])
        fd = (vals[1] - vals[0]) / (2 * step * factor)
        analytical = base[table]["derivatives"][key]
        assert fd == pytest.approx(analytical, rel=0.003, abs=1e-5), (field, fd, analytical)
    for control, coefficient, key in [("elevator", "Cmtot", "Cm"), ("aileron", "Cl'tot", "Cl")]:
        vals = []
        for sign in (-1, 1):
            result = runner.run(
                str(vanilla),
                FlightCondition(alpha_deg=3, mach=0.2, controls={control: sign * 0.01}),
                case_name="fd-control",
            )
            assert result["success"], result
            vals.append(result["total"]["fields"][coefficient])
        assert (vals[1] - vals[0]) / 0.02 == pytest.approx(
            base["stability"]["control_derivatives"][control][key], rel=0.003, abs=1e-6
        )


@pytest.mark.native
def test_sweep_and_isolation(runner, vanilla):
    conditions = [FlightCondition(alpha_deg=a, mach=0.2) for a in (-2, 0, 4)]
    out = runner.sweep(str(vanilla), conditions, case_name="official-sweep")
    assert out["success"], out
    rows = out["results"]
    assert len({r["run_directory"] for r in rows}) == 3
    cl = [r["total"]["fields"]["CLtot"] for r in rows]
    assert cl[0] < cl[1] < cl[2]
    assert Path(out["summary_csv"]).read_text().count("\n") == 4


def fake_binary(tmp_path, body):
    path = tmp_path / "fake-avl"
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)
    return str(path)


def test_timeout_retains_failure_evidence(tmp_path, vanilla):
    binary = fake_binary(tmp_path, "import sys,time\nsys.stdin.read()\ntime.sleep(20)\n")
    r = AVLRunner(binary, str(tmp_path))
    started = time.monotonic()
    out = r.run(str(vanilla), timeout_seconds=0.1)
    assert time.monotonic() - started < 5
    assert not out["success"] and out["error"]["code"] == "TIMEOUT"
    p = Path(out["run_directory"])
    assert json.loads((p / "result.json").read_text())["success"] is False
    assert (p / "commands.txt").exists() and (p / "stdout.log").exists()


def test_health_startup_failure_retains_result(tmp_path):
    binary = tmp_path / "invalid-executable"
    binary.write_text("not an executable format\n")
    binary.chmod(0o755)
    out = AVLRunner(str(binary), str(tmp_path)).health()
    assert not out["success"] and out["error"]["code"] == "EXECUTION_OR_IO_ERROR"
    assert json.loads((Path(out["run_directory"]) / "result.json").read_text()) == out


@pytest.mark.parametrize("marker", ["Trim convergence failed", "** Flow solution is not possible."])
def test_zero_exit_is_not_success(tmp_path, vanilla, marker):
    body = (
        "import sys\nsys.stdin.read()\n"
        "print('Athena Vortex Lattice Program Version 3.52')\n"
        f"print({marker!r})\n"
    )
    out = AVLRunner(fake_binary(tmp_path, body), str(tmp_path)).run(str(vanilla))
    assert not out["success"]
    assert out["error"]["code"] == "SOLVER_REPORTED_ERROR"


def test_sweep_partial_failure(tmp_path, vanilla):
    body = "import sys\nsys.stdin.read()\nprint('Athena Vortex Lattice Program Version 3.52')\n"
    out = AVLRunner(fake_binary(tmp_path, body), str(tmp_path)).sweep(
        str(vanilla), [FlightCondition(alpha_deg=a) for a in (0, 2, 4)]
    )
    assert not out["success"] and out["attempted_count"] == 1 and out["completed_count"] == 0
    assert out["error"]["code"] == "MISSING_OUTPUT"
