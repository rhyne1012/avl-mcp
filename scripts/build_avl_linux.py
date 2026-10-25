"""Build upstream AVL 3.52 on Linux with GNU Fortran and system X11 libraries.

Use a new, disposable source directory prepared by prepare_native_source.py.
Only plotlib build configuration is changed; numerical solver source is unmodified.
The upstream bundled LAPACK/BLAS routines and EISPACK avoid external math libraries.
This helper does not install packages or require an X server.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--fc", default="gfortran", help="GNU Fortran compiler executable")
    parser.add_argument("--cc", default="gcc", help="GNU C compiler executable")
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("This helper requires Linux; use build_avl_macos.py on macOS.")
    src, logs = args.source.resolve(), args.logs.resolve()
    config = src / "plotlib/config.make.gfortranDP"
    if not config.is_file() or not (src / "bin/Makefile.gfortranDP").is_file():
        parser.error("--source must be the extracted official AVL 3.52 source directory.")
    fc, cc = shutil.which(args.fc), shutil.which(args.cc)
    if not fc or not cc or not shutil.which("make"):
        parser.error("GNU Fortran, a C compiler and make are required; see the Ubuntu guide.")
    logs.mkdir(parents=True, exist_ok=True)
    (src / "plotlib/config.make").write_text(
        config.read_text()
        + "\n# Linux overrides; no numerical solver modifications\n"
        + f"FC = {fc}\nCC = {cc}\n"
        + "FFLAGS = -O2 -fdefault-real-8 -fallow-argument-mismatch\n"
        + "INCDIR =\nLINKLIB = -lX11\n"
    )
    commands = [
        ("plotlib", ["make", "-f", "Makefile.all", "lib"]),
        ("eispack", ["make", "-f", "Makefile.gfortran", f"FC={fc}", "FLG=-O2"]),
        (
            "bin",
            [
                "make", "-f", "Makefile.gfortranDP", "avl", f"FC={fc}", "OPT=-O2",
                "PLTLIB=-lX11", "MATRIX=matrix-lapackdp.o", "MLIB=matrix-lapacksubs.o",
                "MLIBSYS=",
            ],
        ),
    ]
    metadata = {
        "commands": commands,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "source": str(src),
        "fortran_compiler": subprocess.check_output([fc, "--version"], text=True).strip(),
        "c_compiler": subprocess.check_output([cc, "--version"], text=True).strip(),
        "math_libraries": "upstream bundled LAPACK/BLAS and EISPACK",
        "numerical_solver_source_modifications": [],
        "results": [],
    }
    manifest = logs / "build-commands.json"
    manifest.write_text(json.dumps(metadata, indent=2) + "\n")
    for directory, command in commands:
        log = logs / f"build-{directory}.log"
        with log.open("w") as stream:
            proc = subprocess.run(
                command, cwd=src / directory, stdout=stream, stderr=subprocess.STDOUT,
            )
        metadata["results"].append({"directory": directory, "returncode": proc.returncode})
        manifest.write_text(json.dumps(metadata, indent=2) + "\n")
        print(directory, proc.returncode, flush=True)
        if proc.returncode:
            raise SystemExit(f"Build failed; see {log}\n{log.read_text()[-6000:]}")
    binary = src / "bin/avl"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise SystemExit(f"Build did not produce an executable: {binary}")
    print("BUILT", binary)


if __name__ == "__main__":
    main()
