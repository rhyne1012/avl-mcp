from pathlib import Path

import pytest

from avl_mcp.models import AVLFailure
from avl_mcp.outputs import parse_derivatives, parse_strips, parse_surfaces, parse_total

FIXTURE = Path(__file__).parent / "fixtures"


def test_reference_output():
    total = parse_total(FIXTURE / "total.mrf")
    assert total["counts"] == {"surfaces": 5, "strips": 41, "vortices": 294}
    assert total["fields"]["CLtot"] == pytest.approx(0.6754170403902354, abs=1e-13)
    assert total["fields"]["Cmtot"] == pytest.approx(0.1199155980932566, abs=1e-13)
    assert total["fields"]["CDff"] != total["fields"]["CDind"]
    st = parse_derivatives(FIXTURE / "stability.mrf", "DERMATS")
    assert st["derivatives"]["CLa"] == pytest.approx(4.980084799923054, abs=1e-12)
    assert st["control_derivatives"]["elevator"]["Cm"] == pytest.approx(-0.02834402045362707)
    sb = parse_derivatives(FIXTURE / "body.mrf", "DERMATB")
    assert sb["derivatives"]["CXu"] == pytest.approx(0.007782277114234557)
    assert sb["axes"] != st["axes"]
    assert len(parse_surfaces(FIXTURE / "surface.mrf")) == 5
    assert sum(len(x["rows"]) for x in parse_strips(FIXTURE / "strips.mrf")) == 41


@pytest.mark.parametrize(
    "old,new",
    [
        ("VERSION 1.0", "VERSION 2.0"),
        ("6.754170403902354E-01", "NaN"),
        ("6.754170403902354E-01", "********"),
        ("Standard axis orientation,  X fwd, Z down", "Geometric axis orientation, X aft, Z up"),
        ("| CLtot", "| unexpected"),
    ],
)
def test_bad_output_fails_closed(tmp_path, old, new):
    p = tmp_path / "total.mrf"
    p.write_text((FIXTURE / "total.mrf").read_text().replace(old, new))
    with pytest.raises(AVLFailure):
        parse_total(p)


def test_truncated_strip_table(tmp_path):
    p = tmp_path / "strips.mrf"
    p.write_text("\n".join((FIXTURE / "strips.mrf").read_text().splitlines()[:-2]))
    with pytest.raises(AVLFailure):
        parse_strips(p)


def test_truncated_control_table(tmp_path):
    p = tmp_path / "stability.mrf"
    text = (FIXTURE / "stability.mrf").read_text()
    p.write_text(text[: text.index("Stability-axis derivatives")])
    with pytest.raises(AVLFailure):
        parse_derivatives(p, "DERMATS")
