from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deployment.dumbmoney.package_windows import (  # noqa: E402
    build_windows_release_candidate,
)


def _repo_mapping(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("repository mapping must be NAME=PATH")
    name, raw_path = value.split("=", 1)
    normalized = name.strip().lower()
    if normalized not in {"blunder", "dummy", "dopey"}:
        raise argparse.ArgumentTypeError(
            "repository name must be blunder, dummy, or dopey"
        )
    path = Path(raw_path)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("repository path must be absolute")
    return normalized, path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or build exactly five private-local DumbMoney Windows runners "
            "into a new staging root. Dry-run is the default."
        )
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "deployment"
            / "dumbmoney"
            / "windows-build.v1.template.json"
        ),
    )
    parser.add_argument(
        "--release-template",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "deployment"
            / "dumbmoney"
            / "release-manifest.template.json"
        ),
    )
    parser.add_argument(
        "--release-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--staging-root",
        type=Path,
        required=True,
        help="new absolute directory; its parent must already exist",
    )
    parser.add_argument(
        "--release-id",
        required=True,
        help="resolved filesystem-safe release identifier",
    )
    parser.add_argument(
        "--repo",
        action="append",
        type=_repo_mapping,
        default=[],
        metavar="NAME=ABSOLUTE_PATH",
        help="clean source checkout; repeat for blunder, dummy, and dopey",
    )
    parser.add_argument(
        "--python-executable",
        type=Path,
        default=Path(sys.executable),
        help="exact Python interpreter pinned by the build spec",
    )
    parser.add_argument(
        "--desktop-executable",
        type=Path,
        help=(
            "absolute path to a signed prebuilt DumbMoney.exe matching the "
            "desktop_artifact digest and size; never built by this command"
        ),
    )
    parser.add_argument(
        "--perform-build",
        action="store_true",
        help=(
            "run the pinned PyInstaller build and create a new release candidate; "
            "never installs or starts services"
        ),
    )
    args = parser.parse_args()
    repo_roots = dict(args.repo)
    report = build_windows_release_candidate(
        spec_path=args.spec.resolve(),
        release_template_path=args.release_template.resolve(),
        release_root=args.release_root.resolve(),
        staging_root=args.staging_root,
        release_id=args.release_id,
        repo_roots=repo_roots,
        perform_build=args.perform_build,
        python_executable=args.python_executable,
        desktop_executable_path=args.desktop_executable,
    )
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "PARTIAL": 2}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
