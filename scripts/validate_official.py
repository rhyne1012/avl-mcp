"""Create reproducible official-case, finite-difference and mesh-sensitivity evidence."""

import argparse
import json
import math
from pathlib import Path

from avl_mcp.mesh import MeshSensitivityRequest
from avl_mcp.models import FlightCondition
from avl_mcp.runner import AVLRunner


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--avl-bin", type=Path, required=True)
    p.add_argument("--work-root", type=Path, required=True)
    p.add_argument("--examples", type=Path, required=True)
    args = p.parse_args()
    root = args.work_root.resolve()
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    runner = AVLRunner(str(args.avl_bin), str(root))
    vanilla = args.examples.resolve() / "vanilla/vanilla.avl"

    def run(model=vanilla, **overrides):
        condition = {"alpha_deg": 3, "mach": 0.2} | overrides
        result = runner.run(str(model), FlightCondition(**condition), case_name="validation")
        assert result["success"], result
        return result

    base = run()
    derivatives = []
    for field, step, coefficient, table, key, unit in [
        ("alpha_deg", 0.01, "CLtot", "stability", "CLa", math.pi / 180),
        ("alpha_deg", 0.01, "Cmtot", "stability", "Cma", math.pi / 180),
        ("beta_deg", 0.01, "CYtot", "stability", "CYb", math.pi / 180),
        ("qc_2v", 0.0001, "Cmtot", "body", "Cmq", 1),
        ("pb_2v", 0.0001, "Cltot", "body", "Clp", 1),
        ("rb_2v", 0.0001, "Cntot", "body", "Cnr", 1),
        ("elevator", 0.01, "Cmtot", "control", "Cm", 1),
        ("aileron", 0.01, "Cl'tot", "control", "Cl", 1),
    ]:
        runs = []
        for sign in (-1, 1):
            delta = sign * step
            condition = (
                {"controls": {field: delta}}
                if table == "control"
                else {field: (3 if field == "alpha_deg" else 0) + delta}
            )
            runs.append(run(**condition))
        numeric = (
            runs[1]["total"]["fields"][coefficient] - runs[0]["total"]["fields"][coefficient]
        ) / (2 * step * unit)
        analytic = (
            base["stability"]["control_derivatives"][field][key]
            if table == "control"
            else base[table]["derivatives"][key]
        )
        relative = abs(numeric - analytic) / max(abs(analytic), 1e-12)
        assert math.isclose(numeric, analytic, rel_tol=0.003, abs_tol=1e-6)
        derivatives.append(
            {
                "variable": field,
                "coefficient": coefficient,
                "reported": analytic,
                "central_difference": numeric,
                "relative_error": relative,
                "step": step,
                "runs": [r["run_directory"] for r in runs],
            }
        )

    sweeps = {}
    for name, unit in [("vanilla", "unspecified"), ("bd", "in")]:
        result = runner.sweep(
            str(args.examples.resolve() / name / f"{name}.avl"),
            [FlightCondition(alpha_deg=a, mach=0.2) for a in (-2, 0, 2, 4, 6)],
            case_name=f"validation-{name}",
            length_unit=unit,
        )
        assert result["success"], result
        sweeps[name] = {
            "run_directory": result["run_directory"],
            "summary_csv": result["summary_csv"],
            "rows": [
                {
                    "alpha_deg": r["total"]["fields"]["Alpha"],
                    **{
                        k: r["total"]["fields"][k]
                        for k in ("CLtot", "Cmtot", "CDind", "CDff", "CDvis")
                    },
                }
                for r in result["results"]
            ],
        }

    # Exercise the same general refinement path exposed to user models over MCP.
    mesh_study = runner.mesh_sensitivity(
        str(vanilla),
        MeshSensitivityRequest(condition=FlightCondition(alpha_deg=3, mach=0.2)),
        case_name="validation-mesh",
    )
    assert mesh_study["success"], mesh_study
    meshes = []
    for level in mesh_study["levels"]:
        result = json.loads(Path(level["artifacts"]["result.json"]).read_text())
        meshes.append(
            {
                "panel_multiplier_each_direction": level["factor"],
                "vortices": result["total"]["counts"]["vortices"],
                "run_directory": result["run_directory"],
                **{k: result["total"]["fields"][k] for k in ("CLtot", "Cmtot", "CDind", "CDff")},
            }
        )

    summary = {
        "success": True,
        "solver": base["solver"],
        "source_archive_sha256": "0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b",
        "baseline": {
            "condition": base["condition"],
            "total": base["total"],
            "run_directory": base["run_directory"],
        },
        "derivatives": derivatives,
        "official_sweeps": sweeps,
        "mesh_sensitivity": meshes,
        "mesh_sensitivity_report": mesh_study["result_json"],
        "scope": "Official examples; internal derivative consistency and limited mesh sensitivity. "
        "Not experimental, CFD, VSPAERO, or general mesh-independence validation.",
    }
    (reports / "official-validation-summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n"
    )

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), layout="constrained")
    for name, sweep in sweeps.items():
        for ax, key, label in zip(
            axes,
            ("CLtot", "Cmtot", "CDff"),
            ("Lift coefficient CL", "Pitch coefficient Cm", "Trefftz induced drag CDff"),
            strict=True,
        ):
            ax.plot(
                [r["alpha_deg"] for r in sweep["rows"]],
                [r[key] for r in sweep["rows"]],
                "o-",
                label=name,
            )
            ax.set(xlabel="Alpha (deg)", ylabel=label)
            ax.grid(alpha=0.25)
    axes[0].legend()
    fig.suptitle("Official AVL 3.52 examples — Mach 0.2, beta/rates/controls zero")
    for ext in ("png", "pdf"):
        fig.savefig(reports / f"official-case-sweeps.{ext}", dpi=180)
    plt.close(fig)
    print(
        json.dumps(
            {
                "success": True,
                "derivative_checks": len(derivatives),
                "max_relative_derivative_error": max(d["relative_error"] for d in derivatives),
                "official_cases": list(sweeps),
                "meshes": len(meshes),
            }
        )
    )


if __name__ == "__main__":
    main()
