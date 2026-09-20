import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from avl_mcp.diagnostics import diagnose, failure_advice
from avl_mcp.runner import AVLRunner


def fake(tmp_path, body):
    p = tmp_path / "avl"
    p.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n" + body)
    p.chmod(0o755)
    return p


def test_missing_executable_has_actionable_evidence(tmp_path):
    out = diagnose(str(tmp_path / "absent"), str(tmp_path / "work"))
    assert not out["success"]
    assert out["checks"]["work_directory"]["status"] == "passed"
    assert out["checks"]["executable"]["exists"] is False
    assert out["error"]["code"] == "EXECUTABLE_NOT_FOUND"
    assert json.loads((Path(out["run_directory"]) / "result.json").read_text()) == out
    assert out["verification"]["numerical_case"]["status"] == "not_run"
    assert out["verification"]["desktop_registration"]["status"] == "not_tested"


def test_invalid_work_root_returns_report_instead_of_crashing(tmp_path):
    p = tmp_path / "file"
    p.write_text("preserved")
    out = diagnose("/missing", str(p / "child"))
    assert out["error"]["code"] == "WORK_ROOT_UNAVAILABLE"
    assert out["checks"]["work_directory"]["status"] == "failed"
    assert "run_directory" not in out
    assert p.read_text() == "preserved"


def test_work_root_permission_failure(monkeypatch, tmp_path):
    original = Path.mkdir

    def denied(self, *args, **kwargs):
        if self == tmp_path / "denied":
            raise PermissionError("Permission denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", denied)
    out = diagnose("/missing", str(tmp_path / "denied"))
    assert not out["success"] and out["error"]["code"] == "WORK_ROOT_UNAVAILABLE"


@pytest.mark.parametrize("banner, expected", [("3.52", True), ("3.40", False)])
def test_startup_not_numerical_or_connection_proof(tmp_path, banner, expected):
    p = fake(tmp_path, f"print('Athena Vortex Lattice Program Version {banner}')\n")
    out = diagnose(str(p), str(tmp_path / "work"))
    assert out["success"] is expected
    assert out["verification"]["numerical_case"]["status"] == "not_run"
    assert out["verification"]["mcp_connection"]["status"] == "not_tested"
    if not expected:
        assert out["error"]["code"] == "UNSUPPORTED_VERSION"


def test_loader_failure_explains_native_dependency(tmp_path):
    p = fake(
        tmp_path, "sys.stderr.write('dyld: Library not loaded: libFortran.dylib\\n')\nsys.exit(1)\n"
    )
    out = diagnose(str(p), str(tmp_path / "work"))
    assert not out["success"]
    assert out["verification"]["native_startup"]["category"] == "native_library"
    assert Path(out["artifacts"]["stderr.log"]).is_file()


def test_wrong_architecture_advice():
    assert (
        failure_advice({"message": "[Errno 86] Bad CPU type in executable"})["category"]
        == "native_architecture_or_format"
    )


def test_python_dependencies_diagnosable_without_site_packages(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "avl_mcp",
            "--diagnose",
            "--avl-bin",
            "/missing",
            "--work-root",
            str(tmp_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The diagnostic import probe must preserve the -S runtime restriction.
    data = json.loads(completed.stdout)
    assert data["checks"]["python"]["status"] == "failed"
    assert data["error"]["code"] == "PYTHON_DEPENDENCIES"
    assert completed.returncode == 1


@pytest.mark.native
def test_real_health_layers(runner):
    out = runner.health()
    assert out["success"], out
    assert out["verification"]["native_startup"]["status"] == "passed"
    assert out["solver"]["version"] == "3.52"
    assert out["checks"]["python"]["status"] == "passed"
    assert out["checks"]["work_directory"]["status"] == "passed"


@pytest.mark.native
def test_bad_input_root_is_separate_from_native_startup(runner, tmp_path):
    out = AVLRunner(str(runner.executable), str(tmp_path), str(tmp_path / "absent")).health()
    assert not out["success"]
    assert out["verification"]["native_startup"]["status"] == "passed"
    assert out["error"]["code"] == "INPUT_ROOT_UNAVAILABLE"
