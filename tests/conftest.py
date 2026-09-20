import os
from pathlib import Path

import pytest

from avl_mcp.runner import AVLRunner

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def vanilla():
    return REPO / "examples/official/vanilla/vanilla.avl"


@pytest.fixture
def bd():
    return REPO / "examples/official/bd/bd.avl"


@pytest.fixture
def runner(tmp_path):
    binary = os.environ.get("AVL_BIN")
    if not binary:
        pytest.skip("Set AVL_BIN to test the actual AVL 3.52 solver.")
    root = os.environ.get("AVL_TEST_WORK_ROOT", str(tmp_path))
    return AVLRunner(binary, root)
