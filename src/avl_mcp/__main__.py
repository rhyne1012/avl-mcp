"""Launch a standalone local MCP server without editing any client configuration."""

import argparse
import os

from . import __version__
from .runner import AVLRunner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"avl-mcp {__version__}")
    parser.add_argument("--avl-bin", default=os.environ.get("AVL_BIN"))
    parser.add_argument("--work-root", default=os.environ.get("AVL_MCP_WORK_ROOT"))
    parser.add_argument("--input-root", default=os.environ.get("AVL_MCP_INPUT_ROOT"))
    parser.add_argument("--max-vortices", type=int, default=5000)
    args = parser.parse_args()
    if not args.avl_bin or not args.work_root:
        parser.error("Set --avl-bin/AVL_BIN and --work-root/AVL_MCP_WORK_ROOT.")
    if not 1 <= args.max_vortices <= 20000:
        parser.error("--max-vortices must be between 1 and 20000.")
    from .server import make_server

    make_server(AVLRunner(args.avl_bin, args.work_root, args.input_root, args.max_vortices)).run(
        transport="stdio"
    )


if __name__ == "__main__":
    main()
