"""Launch a standalone local MCP server without editing any client configuration."""

import argparse
import json
import os

from . import __version__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"avl-mcp {__version__}")
    parser.add_argument("--avl-bin", default=os.environ.get("AVL_BIN"))
    parser.add_argument("--work-root", default=os.environ.get("AVL_MCP_WORK_ROOT"))
    parser.add_argument("--input-root", default=os.environ.get("AVL_MCP_INPUT_ROOT"))
    parser.add_argument("--max-vortices", type=int, default=5000)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--diagnose", action="store_true", help="Print installation diagnosis JSON and exit"
    )
    modes.add_argument(
        "--check-mcp", action="store_true", help="Diagnose and test a fresh stdio connection"
    )
    args = parser.parse_args()
    if not args.avl_bin or not args.work_root:
        parser.error("Set --avl-bin/AVL_BIN and --work-root/AVL_MCP_WORK_ROOT.")
    if not 1 <= args.max_vortices <= 20000:
        parser.error("--max-vortices must be between 1 and 20000.")
    if args.diagnose or args.check_mcp:
        from .diagnostics import check_stdio, diagnose

        result = diagnose(args.avl_bin, args.work_root, args.input_root, args.max_vortices)
        if args.check_mcp:
            result = check_stdio(
                result, args.avl_bin, args.work_root, args.input_root, args.max_vortices
            )
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        raise SystemExit(0 if result["success"] else 1)
    from .runner import AVLRunner
    from .server import make_server

    make_server(AVLRunner(args.avl_bin, args.work_root, args.input_root, args.max_vortices)).run(
        transport="stdio"
    )


if __name__ == "__main__":
    main()
