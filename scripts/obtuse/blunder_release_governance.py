from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blunder.release_governance import build_release_manifest, verify_release_manifest, write_release_manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Prepare or verify private Blunder release provenance.")
    result.add_argument("action", choices=("prepare", "verify"))
    result.add_argument("--root", required=True)
    result.add_argument("--manifest", required=True)
    result.add_argument("--readiness")
    result.add_argument("--proof")
    result.add_argument("--checks")
    return result


def read_object(path: str, label: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return value


def main() -> int:
    arguments = parser().parse_args()
    signing_key = os.environ.get("BLUNDER_RELEASE_SIGNING_KEY", "")
    if arguments.action == "verify":
        result = verify_release_manifest(read_object(arguments.manifest, "Manifest"), signing_key)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "PASS" else 1
    if not arguments.readiness or not arguments.proof or not arguments.checks:
        raise ValueError("Prepare requires --readiness, --proof, and --checks.")
    checks_value = json.loads(Path(arguments.checks).read_text(encoding="utf-8"))
    if not isinstance(checks_value, list) or any(not isinstance(item, dict) for item in checks_value):
        raise ValueError("Checks must be a JSON object list.")
    manifest = build_release_manifest(
        Path(arguments.root).resolve(),
        read_object(arguments.readiness, "Readiness"),
        read_object(arguments.proof, "Proof"),
        checks_value,
        signing_key or None,
    )
    write_release_manifest(Path(arguments.manifest).resolve(), manifest)
    print(json.dumps(manifest, indent=2))
    return 0 if manifest["status"] == "READY_FOR_EXACT_OPERATOR_CONFIRMATION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
