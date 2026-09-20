"""Build upstream AVL 3.52 using a task-local conda-forge compiler/X11 prefix.

Source is an extracted, disposable copy of the official tarball. No solver source
is patched. Requires Xcode Command Line Tools. Does not install system packages.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--compiler-prefix", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    args = parser.parse_args()
    src, deps, logs = args.source.resolve(), args.compiler_prefix.resolve(), args.logs.resolve()
    logs.mkdir(parents=True, exist_ok=True)
    fc = str(deps / "bin/gfortran")
    sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
    env = dict(
        os.environ,
        SDKROOT=sdk,
        CONDA_PREFIX=str(deps),
        PATH=f"{deps}/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    )
    (src / "plotlib/config.make").write_text(
        (src / "plotlib/config.make.gfortranDP").read_text()
        + f"\n# Task-local overrides; no numerical solver modifications\n"
        f"FC = {fc}\nCC = /usr/bin/clang\n"
        f"FFLAGS = -O2 -fdefault-real-8 -fallow-argument-mismatch -isysroot {sdk}\n"
        f"INCDIR = -I{deps}/include\nLINKLIB = -L{deps}/lib -lX11\n"
    )
    commands = [
        ("plotlib", ["make", "-f", "Makefile.all", "lib"]),
        ("eispack", ["make", "-f", "Makefile.gfortran", f"FC={fc}", f"FLG=-O2 -isysroot {sdk}"]),
        (
            "bin",
            [
                "make",
                "-f",
                "Makefile.gfortranDP",
                "avl",
                f"FC={fc}",
                f"OPT=-O2 -isysroot {sdk}",
                f"PLTLIB=-L{deps}/lib -lX11",
                f"LFLG=-Wl,-rpath,{deps}/lib",
            ],
        ),
    ]
    for directory, command in commands:
        log = logs / f"build-{directory}.log"
        with log.open("w") as stream:
            proc = subprocess.run(
                command, cwd=src / directory, env=env, stdout=stream, stderr=subprocess.STDOUT
            )
        print(directory, proc.returncode, flush=True)
        if proc.returncode:
            raise SystemExit(log.read_text()[-6000:])
    (logs / "build-commands.json").write_text(
        json.dumps(
            {
                "commands": commands,
                "sdk": sdk,
                "compiler_prefix": str(deps),
                "source": str(src),
                "numerical_solver_source_modifications": [],
            },
            indent=2,
        )
        + "\n"
    )
    print("BUILT", src / "bin/avl")


if __name__ == "__main__":
    main()
