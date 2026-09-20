"""Bundle a locally built AVL with its non-system dylibs; no system modifications.

For local use. Preserve third-party licenses before distributing a binary bundle.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def output(*args):
    return subprocess.check_output(args, text=True)


def deps(path):
    return [
        line.strip().split(" (", 1)[0] for line in output("otool", "-L", str(path)).splitlines()[1:]
    ]


def rpaths(path):
    lines = output("otool", "-l", str(path)).splitlines()
    found = []
    for i, line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH":
            found.append(lines[i + 2].strip().split("path ", 1)[1].split(" (offset", 1)[0])
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--library-prefix", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    dest = args.destination.resolve()
    if dest.exists():
        raise SystemExit("Destination already exists; choose a new directory.")
    (dest / "bin").mkdir(parents=True)
    (dest / "lib").mkdir()
    binary = dest / "bin/avl"
    shutil.copy2(args.binary, binary)
    queue = [binary]
    processed = set()
    records = []
    while queue:
        target = queue.pop(0)
        if target in processed:
            continue
        processed.add(target)
        target.chmod(0o755)
        links = deps(target)
        for link in links:
            if link.startswith(("/usr/lib/", "/System/Library/")):
                continue
            name = Path(link).name
            source = args.library_prefix / "lib" / name
            library = dest / "lib" / name
            if not source.is_file():
                raise SystemExit(f"Unresolved non-system dependency: {link}")
            if not library.exists():
                shutil.copy2(source.resolve(), library)
                records.append(
                    {
                        "source": str(source),
                        "file": f"lib/{name}",
                        "original_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                )
                queue.append(library)
            if link != f"@rpath/{name}":
                subprocess.run(
                    ["install_name_tool", "-change", link, f"@rpath/{name}", str(target)],
                    check=True,
                    capture_output=True,
                )
        if target != binary:
            subprocess.run(
                ["install_name_tool", "-id", f"@rpath/{target.name}", str(target)],
                check=True,
                capture_output=True,
            )
        for entry in rpaths(target):
            subprocess.run(
                ["install_name_tool", "-delete_rpath", entry, str(target)],
                check=True,
                capture_output=True,
            )
        new_path = "@executable_path/../lib" if target == binary else "@loader_path"
        subprocess.run(
            ["install_name_tool", "-add_rpath", new_path, str(target)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(target)], check=True, capture_output=True
        )
    manifest = {
        "binary_source": str(args.binary),
        "library_prefix": str(args.library_prefix),
        "libraries": records,
        "bundle_hashes": {
            str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(processed)
        },
    }
    (dest / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"destination": str(dest), "libraries": len(records)}))


if __name__ == "__main__":
    main()
