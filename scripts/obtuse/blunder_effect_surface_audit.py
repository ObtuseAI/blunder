"""Audit constitutional adapters against the monitored effect-surface registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Blunder constitutional effect-surface coverage.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    options = parse_arguments()
    root = options.root.resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from blunder.effect_surface_registry import audit_effect_surface_coverage

    report = audit_effect_surface_coverage(root)
    write_report(options.output.resolve(), report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
