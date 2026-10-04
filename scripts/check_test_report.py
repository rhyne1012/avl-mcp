"""Fail an acceptance gate on absent, empty, failed or skipped JUnit results."""

import argparse
import json
import xml.etree.ElementTree as ET


def check(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    counts = {
        key: sum(case.find(key) is not None for case in cases)
        for key in ("failure", "error", "skipped")
    }
    if not cases or any(counts.values()) or list(root.iter("error")):
        raise ValueError(f"Acceptance requires executed tests with zero failures/skips: {counts}")
    return {"passed": len(cases), **counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.report)))
    except (OSError, ValueError, ET.ParseError) as exc:
        parser.exit(1, f"Validation incomplete: {exc}\n")


if __name__ == "__main__":
    main()
