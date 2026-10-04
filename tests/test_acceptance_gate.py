"""CI must not equate missing native coverage or skipped tests with acceptance."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "content",
    [
        "<testsuites/>",
        "<testsuite><testcase><skipped/></testcase></testsuite>",
        "<testsuite><testcase><failure/></testcase></testsuite>",
        "<testsuite><testcase><error/></testcase></testsuite>",
        '<testsuite><testcase/><error message="collection failed"/></testsuite>',
        "<broken",
        None,
    ],
)
def test_incomplete_report_cannot_pass(tmp_path, content):
    report = tmp_path / "report.xml"
    if content is not None:
        report.write_text(content)
    process = subprocess.run(
        [sys.executable, str(REPO / "scripts/check_test_report.py"), str(report)],
        capture_output=True,
        text=True,
    )
    assert process.returncode != 0
    assert "Validation incomplete" in process.stderr


def test_complete_report_can_pass(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text("<testsuite><testcase/><testcase/></testsuite>")
    process = subprocess.run(
        [sys.executable, str(REPO / "scripts/check_test_report.py"), str(report)],
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    assert '"passed": 2' in process.stdout


def test_missing_native_binary_is_a_failure(tmp_path):
    env = dict(os.environ, AVL_BIN=str(tmp_path / "missing-avl"))
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--require-native",
            "--collect-only",
            "tests/test_runner.py",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    assert process.returncode != 0
    assert "acceptance incomplete" in process.stderr


def test_native_deselection_cannot_pass():
    env = dict(os.environ, AVL_BIN=sys.executable)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--require-native",
            "--collect-only",
            "-m",
            "not native",
            "tests/test_runner.py",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    assert process.returncode != 0
    assert "native tests in the selected collection" in process.stderr
