import copy
import json
import math
from pathlib import Path

import pytest

from avl_mcp.contract import make_contract
from avl_mcp.jobs import JobManager
from avl_mcp.models import FlightCondition, References
from avl_mcp.outputs import parse_derivatives, parse_total
from avl_mcp.runner import AVLRunner

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "unit, expected", [("unspecified", "Lunit"), ("m", "m"), ("ft", "ft"), ("in", "in")]
)
def test_contract_covers_fields_without_changing_native_values(unit, expected):
    record = {
        "total": parse_total(FIXTURES / "total.mrf"),
        "geometry_length_unit": unit,
        "outputs": ["total", "stability", "body", "strips"],
        "stability": parse_derivatives(FIXTURES / "stability.mrf", "DERMATS"),
        "body": parse_derivatives(FIXTURES / "body.mrf", "DERMATB"),
    }
    before = copy.deepcopy(record)
    c = make_contract(record)
    assert record == before
    assert set(c["total_fields"]) == set(record["total"]["fields"])
    assert c["total_fields"]["Sref"]["unit"] == expected + "^2"
    assert c["moment_reference"]["coordinates"] == [0.5, 0, 0]
    assert c["units"]["geometry_converted"] is False
    assert c["total_fields"]["CLtot"]["axes"] == "standard_stability"
    assert c["total_fields"]["Cltot"]["axes"] == "standard_body"
    assert c["total_fields"]["Cl'tot"]["axes"] == "standard_stability"
    st, sb = (c["derivative_tables"][k] for k in ("stability", "body"))
    assert st["fields"]["CLa"]["unit"] == "rad^-1"
    assert st["control_derivatives"]["unit"] == "CONTROL_unit^-1"
    assert st["variables"]["p"]["axes"] != sb["variables"]["p"]["axes"]
    assert sb["variables"]["u"]["definition"] == "u/V0"
    assert c["strip_fields"]["ai"]["unit"] == "1"
    assert "999" in c["strip_fields"]["C.P.x/c"]["definition"]


@pytest.mark.native
@pytest.mark.parametrize("model_name", ["vanilla", "bd"])
def test_actual_references_units_axes_and_query_survive_restart(runner, request, model_name):
    model = request.getfixturevalue(model_name)
    refs = References(sref=13, cref=1.7, bref=14, xref=0.61, yref=0.03, zref=-0.02)
    out = runner.run(
        str(model),
        FlightCondition(alpha_deg=7, beta_deg=4, mach=0.2),
        references=refs,
        length_unit="ft",
    )
    assert out["success"], out
    c = out["result_contract"]
    assert c["references"]["Sref"] == 13
    assert c["moment_reference"]["coordinates"] == [0.61, 0.03, -0.02]
    assert c["moment_reference"]["unit"] == "ft"
    f = out["total"]["fields"]
    a, b = map(math.radians, (f["Alpha"], f["Beta"]))
    # Independent projection of native body forces onto the incoming wind direction.
    force = [f["CXtot"], f["CYtot"], f["CZtot"]]
    velocity = [math.cos(a) * math.cos(b), math.sin(b), math.sin(a) * math.cos(b)]
    cd = -sum(x * y for x, y in zip(force, velocity))
    # Upstream adds CDp to CDtot without its cos(beta) body projection.
    cdp = 0.017 if model_name == "bd" else 0
    offset = cdp * (1 - math.cos(b))
    assert out["derived"]["wind_forces"]["CD"] - cd == pytest.approx(
        offset * math.cos(b), abs=1e-12
    )
    codes = out["result_context"]["native_limitation_codes"]
    assert ("NATIVE_FORCE_PROJECTION_DIFFERENCE" in codes) == (model_name == "bd")
    assert abs(cd - f["CDtot"]) > 1e-6
    matrix = c["axes"]["standard_stability"]["body_to_stability"]
    transformed = [sum(x * y for x, y in zip(row, force)) for row in matrix]
    assert transformed == pytest.approx([-f["CDtot"] + offset, f["CYtot"], -f["CLtot"]], abs=1e-13)
    saved = json.loads(Path(out["artifacts"]["result-contract.json"]).read_text())
    assert saved == c
    # A new reader with no native executable still supplies context and selected metadata.
    reader = JobManager(AVLRunner("/unavailable", str(runner.work_root)))
    queried = reader.results(
        out["job_id"], fields=["body.derivatives.Cmq", "result_contract.units"]
    )
    assert queried["rows"][0]["result_context"] == out["result_context"]
    assert queried["rows"][0]["fields"]["result_contract.units"]["length"] == "ft"
    assert queried["solver_executed"] is False


@pytest.mark.native
def test_native_stability_alpha_rate_exception_is_identified(runner, vanilla):
    a0 = math.radians(7)
    ps, rs = 0.025, 0.03

    def cond(a):
        return FlightCondition(
            alpha_deg=math.degrees(a),
            beta_deg=3,
            mach=0.2,
            pb_2v=ps * math.cos(a) - rs * math.sin(a),
            qc_2v=0.002,
            rb_2v=ps * math.sin(a) + rs * math.cos(a),
        )

    h = 0.0001
    result = runner.sweep(
        str(vanilla), [cond(a0 - h), cond(a0), cond(a0 + h)], outputs=["total", "stability", "body"]
    )
    assert result["success"], result
    low, base, high = result["results"]
    native = base["stability"]["derivatives"]["Cma"]
    fd_fixed = (high["total"]["fields"]["Cmtot"] - low["total"]["fields"]["Cmtot"]) / (2 * h)
    assert abs(fd_fixed - native) > 0.001
    assert "NATIVE_ST_ALPHA_NONZERO_RATES" in base["result_context"]["native_limitation_codes"]
    # Independently verify the source-level diagnosis: the native derivative uses the
    # opposite p/r rotation sensitivity for standard axes. Preserve, do not "fix", it.
    c0 = cond(a0)
    cases = [
        c0.model_copy(
            update={
                "alpha_deg": math.degrees(a0 + d),
                "pb_2v": c0.pb_2v + c0.rb_2v * d,
                "rb_2v": c0.rb_2v - c0.pb_2v * d,
            }
        )
        for d in (-h, h)
    ]
    other = runner.sweep(str(vanilla), cases, outputs=["total"])
    assert other["success"], other
    fd_native = (
        other["results"][1]["total"]["fields"]["Cmtot"]
        - other["results"][0]["total"]["fields"]["Cmtot"]
    ) / (2 * h)
    assert fd_native == pytest.approx(native, rel=1e-5, abs=1e-6)


@pytest.mark.native
def test_body_velocity_derivatives_chain_rule(runner, vanilla):
    result = runner.sweep(
        str(vanilla),
        [FlightCondition(alpha_deg=a, beta_deg=3, mach=0.2) for a in (6.99, 7, 7.01)],
        outputs=["total", "body"],
    )
    assert result["success"], result
    low, base, high = result["results"]
    a, b = math.radians(7), math.radians(3)
    for coefficient in ("CX", "CY", "CZ", "Cl", "Cm", "Cn"):
        d = base["body"]["derivatives"]
        analytic = (
            -math.sin(a) * math.cos(b) * d[coefficient + "u"]
            + math.cos(a) * math.cos(b) * d[coefficient + "w"]
        )
        fd = (
            high["total"]["fields"][coefficient + "tot"]
            - low["total"]["fields"][coefficient + "tot"]
        ) / math.radians(0.02)
        assert fd == pytest.approx(analytic, rel=0.003, abs=1e-6)


@pytest.mark.native
def test_selective_output_and_legacy_context(runner, vanilla, tmp_path):
    out = runner.run(str(vanilla), outputs=["total"])
    assert out["success"], out
    assert out["result_contract"]["derivative_tables"] == {}
    assert "strip_fields" not in out["result_contract"]
    legacy = tmp_path / "runs" / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "result.json").write_text(json.dumps({"success": True, "total": out["total"]}))
    query = JobManager(AVLRunner("/missing", str(tmp_path))).results("legacy")
    assert query["rows"][0]["result_context"]["schema_version"] is None


@pytest.mark.native
def test_stability_rate_variables_and_control_gain_metadata(runner, vanilla):
    from avl_mcp.geometry import inspect_geometry

    a = math.radians(7)
    conditions = [FlightCondition(alpha_deg=7, beta_deg=3, mach=0.2)]
    h = 0.0001
    for variable in ("p", "r"):
        for sign in (-1, 1):
            p, r = (sign * h, 0) if variable == "p" else (0, sign * h)
            conditions.append(
                FlightCondition(
                    alpha_deg=7,
                    beta_deg=3,
                    mach=0.2,
                    pb_2v=p * math.cos(a) - r * math.sin(a),
                    rb_2v=p * math.sin(a) + r * math.cos(a),
                )
            )
    out = runner.sweep(str(vanilla), conditions, outputs=["total", "stability"])
    assert out["success"], out
    base = out["results"][0]
    for variable, start in (("p", 1), ("r", 3)):
        for key, total in (("Cl", "Cl'tot"), ("Cn", "Cn'tot"), ("CY", "CYtot")):
            low, high = out["results"][start : start + 2]
            fd = (high["total"]["fields"][total] - low["total"]["fields"][total]) / (2 * h)
            assert fd == pytest.approx(
                base["stability"]["derivatives"][key + variable], rel=0.003, abs=1e-6
            )
    actual = base["result_contract"]["controls"]["section_definitions"]
    controls = [
        c
        for s in inspect_geometry(str(vanilla)).surfaces
        for section in s["sections"]
        for c in section["controls"]
    ]
    assert len(actual) == len(controls)
    for declaration, source in zip(actual, controls):
        assert all(declaration[k] == v for k, v in source.items())
    assert any(c["duplicate_sign"] == -1 for c in actual)
