"""Compare isolated vs shared-process execution on unchanged official models."""

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

from avl_mcp.geometry import sha256
from avl_mcp.models import FlightCondition
from avl_mcp.runner import AVLRunner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--avl-bin", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--examples", type=Path, required=True)
    parser.add_argument("--count", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.count <= 500:
        parser.error("count must be between 1 and 500")
    runner = AVLRunner(str(args.avl_bin), str(args.work_root))
    cases = [
        FlightCondition(alpha_deg=(i % 8) - 2, beta_deg=(i % 3) - 1, mach=0.2)
        for i in range(args.count)
    ]
    reports = []
    for name in ["vanilla", "bd"]:
        model = args.examples / name / (name + ".avl")
        assert runner.run(str(model), cases[0], outputs=["total"])["success"]
        timings = []
        maximum = 0.0
        for _ in range(3):
            started = time.perf_counter()
            singles = [runner.run(str(model), c, outputs=["total"]) for c in cases]
            isolated = time.perf_counter() - started
            assert all(r["success"] for r in singles)
            started = time.perf_counter()
            batch = runner.sweep(str(model), cases, outputs=["total"])
            shared = time.perf_counter() - started
            assert batch["success"], batch
            for single, batched in zip(singles, batch["results"], strict=True):
                for field, value in single["total"]["fields"].items():
                    maximum = max(maximum, abs(value - batched["total"]["fields"][field]))
            timings.append({"isolated_seconds": isolated, "shared_seconds": shared})
        assert maximum < 1e-10, maximum
        a = statistics.median(t["isolated_seconds"] for t in timings)
        b = statistics.median(t["shared_seconds"] for t in timings)
        reports.append(
            {
                "model": name,
                "model_sha256": sha256(model),
                "conditions": len(cases),
                "outputs": ["total"],
                "mach": 0.2,
                "repeats": timings,
                "median_isolated_seconds": a,
                "median_shared_seconds": b,
                "median_speedup": a / b,
                "max_abs_field_difference": maximum,
            }
        )
    report = {
        "success": True,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "solver_sha256": sha256(args.avl_bin),
        "results": reports,
        "scope": "Host-specific end-to-end runner timing, unchanged official meshes; "
        "no promised speedup for other models or a desktop transport.",
    }
    args.work_root.mkdir(parents=True, exist_ok=True)
    (args.work_root / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
