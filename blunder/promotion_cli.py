from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from blunder.api.contracts import public_value, strict_json_loads
from blunder.promotion_policy import (
    PromotionLedger,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
    parse_promotion_evidence,
)


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate one candidate against Blunder's fixed promotion thresholds.")
    parser.add_argument("--root", required=True, help="Authorized repository root")
    parser.add_argument("--scope", required=True, choices=[scope.value for scope in PromotionScopeV1])
    parser.add_argument("--evidence", required=True, help="Strict PromotionEvidenceV1 JSON inside the repository")
    return parser.parse_args(arguments)


def run(arguments: list[str]) -> tuple[dict[str, object], int]:
    parsed = parse_arguments(arguments)
    root = Path(parsed.root).resolve()
    evidence_path = Path(parsed.evidence).resolve()
    if not root.is_dir():
        raise ValueError(f"Promotion repository root does not exist: {root}")
    if not evidence_path.is_file() or not evidence_path.is_relative_to(root):
        raise ValueError("Promotion evidence must be an existing file inside the authorized repository.")
    raw = strict_json_loads(evidence_path.read_text(encoding="utf-8"), f"Promotion evidence {evidence_path}")
    evidence = parse_promotion_evidence(raw)
    decision = evaluate_autonomous_promotion(PromotionScopeV1(str(parsed.scope)), evidence)
    record = PromotionLedger(root).append(decision, datetime.now(timezone.utc))
    result = {
        "schema": "obtuse.blunder.promotion-cli-result.v1",
        "decision": public_value(decision),
        "ledgerRecord": record,
    }
    return result, 0 if decision.promoted else 2


def main() -> int:
    result, exit_code = run(sys.argv[1:])
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
