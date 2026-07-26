from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blunder.app_blueprint import compile_blueprint
from blunder.asset_intelligence import asset_intelligence_report
from blunder.design_system import compile_design_system
from blunder.device_matrix import build_device_matrix
from blunder.game_studio import game_studio_report
from blunder.mobile_foundry import mobile_project_report
from blunder.polyglot_intelligence import polyglot_index
from blunder.product_arena import load_product_suite, parse_measurement, product_arena_report
from blunder.visual_judgment import visual_judgment_report
from blunder.web_lab import web_production_report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Operate the first-party Blunder Product Studio.")
    result.add_argument("action", choices=("audit", "blueprint", "design", "device", "assets", "polyglot", "arena", "visual"))
    result.add_argument("--root", required=True)
    result.add_argument("--report", required=True)
    result.add_argument("--input")
    result.add_argument("--domain", choices=("mobile", "web", "game"))
    result.add_argument("--locale", default="en-US")
    return result


def read_object(path: str | None, label: str) -> dict[str, object]:
    if not path:
        raise ValueError(f"{label} requires --input.")
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} input must be a JSON object.")
    return value


def audit(root: Path, domain: str | None) -> dict[str, object]:
    operations = {"mobile": mobile_project_report, "web": web_production_report, "game": game_studio_report}
    if domain not in operations:
        raise ValueError("Product audit requires --domain mobile, web, or game.")
    return operations[domain](root)


def build_report(arguments: argparse.Namespace) -> dict[str, object]:
    root = Path(arguments.root).resolve()
    if arguments.action == "audit":
        return audit(root, arguments.domain)
    if arguments.action == "blueprint":
        return compile_blueprint(read_object(arguments.input, "Blueprint"))
    if arguments.action == "design":
        return compile_design_system(read_object(arguments.input, "Design system"))
    if arguments.action == "device":
        return build_device_matrix(arguments.locale, ())
    if arguments.action == "assets":
        provenance = root / "assets.provenance.json"
        return asset_intelligence_report(
            root,
            {"maximumAssetBytes": 5_000_000, "maximumTotalBytes": 50_000_000, "maximumImagePixels": 16_000_000},
            provenance if provenance.is_file() else None,
            ("first-party", "mit", "apache-2.0", "cc0"),
        )
    if arguments.action == "polyglot":
        return polyglot_index(root)
    if arguments.action == "visual":
        value = read_object(arguments.input, "Visual judgment")
        raw_cases = value.get("cases")
        cases = [item for item in raw_cases if isinstance(item, dict)] if isinstance(raw_cases, list) else []
        return visual_judgment_report(root, cases)
    value = read_object(arguments.input, "Product arena") if arguments.input else {"measurements": [], "baselines": []}
    raw_measurements = value.get("measurements")
    raw_baselines = value.get("baselines")
    measurements = [parse_measurement(item) for item in raw_measurements if isinstance(item, dict)] if isinstance(raw_measurements, list) else []
    baselines = [item for item in raw_baselines if isinstance(item, dict)] if isinstance(raw_baselines, list) else []
    suite = load_product_suite(REPO_ROOT / "configs" / "obtuse" / "blunder-product-evaluation-suite.json")
    return product_arena_report(suite, measurements, baselines)


def write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(report), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    arguments = parser().parse_args()
    report = build_report(arguments)
    write_report(Path(arguments.report).resolve(), report)
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") in {
        "PASS",
        "READY_FOR_PRODUCT_PLANNING",
        "READY_FOR_EXECUTABLE_PROOF",
        "READY_FOR_HEADLESS_PROOF",
        "READY_FOR_EXECUTION",
        "NO_CANDIDATES",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
