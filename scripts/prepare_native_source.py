"""Verify and unpack the pinned official AVL source into a new disposable directory."""

import argparse
import hashlib
import tarfile
from pathlib import Path, PurePosixPath

SOURCE_SHA256 = "0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b"
SOURCE_ROOT = "AVL3.52rel09032025"


def prepare(archive, destination):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Official AVL 3.52 archive SHA-256 mismatch.")
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            if member.name == "._" + SOURCE_ROOT:
                continue  # Top-level AppleDouble metadata in the official archive.
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or name.parts[0] != SOURCE_ROOT:
                raise ValueError(f"Unexpected archive path: {name}")
            # Upstream includes development symlinks; compilation uses regular source files only.
            if member.isdir():
                (destination / member.name).mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target = destination / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.extractfile(member).read())
    return destination / SOURCE_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(prepare(args.archive, args.destination))


if __name__ == "__main__":
    main()
