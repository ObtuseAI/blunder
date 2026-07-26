from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blunder.evaluation_arena import arena_report, load_suite, parse_measurement


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the first-party Blunder agent evaluation arena.")
    result.add_argument("--suite", required=True)
    result.add_argument("--measurements", required=True)
    result.add_argument("--baselines", required=True)
    result.add_argument("--report", required=True)
    return result


def object_list(path: Path, label: str) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a JSON object list.")
    return value


def main() -> int:
    arguments = parser().parse_args()
    measurements = [
        parse_measurement(item)
        for item in object_list(Path(arguments.measurements), "Measurements")
    ]
    report = arena_report(
        load_suite(Path(arguments.suite)),
        measurements,
        object_list(Path(arguments.baselines), "Baselines"),
    )
    output = Path(arguments.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"PASS", "NO_CANDIDATES"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
