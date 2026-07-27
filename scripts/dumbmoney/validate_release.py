from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deployment.dumbmoney.release import validate_release_bundle  # noqa: E402


def _repo_mapping(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("repository roots use NAME=PATH")
    name, raw_path = value.split("=", 1)
    if not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("repository roots use NAME=PATH")
    return name.strip().lower(), Path(raw_path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a DumbMoney release without installing services or contacting brokers."
        )
    )
    parser.add_argument(
        "--release-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="root against which release paths are resolved",
    )
    parser.add_argument(
        "--release-manifest",
        type=Path,
        default=REPOSITORY_ROOT
        / "deployment"
        / "dumbmoney"
        / "release-manifest.template.json",
    )
    parser.add_argument(
        "--fund-lock",
        type=Path,
        default=REPOSITORY_ROOT
        / "deployment"
        / "dumbmoney"
        / "fund.lock.template.json",
    )
    parser.add_argument(
        "--repo",
        action="append",
        type=_repo_mapping,
        default=[],
        metavar="NAME=PATH",
        help="current checkout to compare with a pinned Git SHA; repeatable",
    )
    parser.add_argument(
        "--git-executable",
        type=Path,
        help="absolute non-link path to the exact Git executable used for --repo checks",
    )
    parser.add_argument(
        "--git-executable-sha256",
        help="lowercase SHA-256 pin for --git-executable",
    )
    args = parser.parse_args()
    if (args.git_executable is None) != (args.git_executable_sha256 is None):
        parser.error(
            "--git-executable and --git-executable-sha256 must be supplied together"
        )
    repo_roots = dict(args.repo)
    report = validate_release_bundle(
        release_manifest_path=args.release_manifest.resolve(),
        fund_lock_path=args.fund_lock.resolve(),
        release_root=args.release_root.resolve(),
        repo_roots=repo_roots,
        git_executable=args.git_executable,
        git_executable_sha256=args.git_executable_sha256,
    )
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "PARTIAL": 2}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
