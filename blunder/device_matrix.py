from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


DEVICE_MATRIX_SCHEMA = "obtuse.blunder.device-matrix.v1"


@dataclass(frozen=True)
class DeviceProfile:
    profile_id: str
    category: str
    width: int
    height: int
    device_scale_factor: float
    touch: bool
    orientation: str
    color_scheme: str
    reduced_motion: bool
    locale: str
    offline: bool


def base_profiles(locale: str) -> tuple[DeviceProfile, ...]:
    if len(locale.strip()) < 2 or len(locale) > 20:
        raise ValueError("Device locale must contain from 2 through 20 characters.")
    return (
        DeviceProfile("desktop-wide-light", "desktop", 1440, 1000, 1.0, False, "landscape", "light", False, locale, False),
        DeviceProfile("desktop-keyboard-dark", "desktop", 1280, 800, 1.0, False, "landscape", "dark", False, locale, False),
        DeviceProfile("tablet-touch", "tablet", 820, 1180, 2.0, True, "portrait", "light", False, locale, False),
        DeviceProfile("phone-compact", "phone", 390, 844, 3.0, True, "portrait", "light", False, locale, False),
        DeviceProfile("phone-landscape", "phone", 844, 390, 3.0, True, "landscape", "dark", False, locale, False),
        DeviceProfile("phone-reduced-motion", "accessibility", 390, 844, 3.0, True, "portrait", "dark", True, locale, False),
        DeviceProfile("phone-offline", "resilience", 390, 844, 3.0, True, "portrait", "light", False, locale, True),
        DeviceProfile("desktop-zoom-proxy", "accessibility", 720, 900, 2.0, False, "portrait", "light", True, locale, False),
    )


def build_device_matrix(locale: str, required_categories: Sequence[str]) -> dict[str, object]:
    profiles = base_profiles(locale)
    available = frozenset(profile.category for profile in profiles)
    requested = frozenset(str(item).strip().lower() for item in required_categories)
    unknown = requested.difference(available)
    if unknown:
        raise ValueError(f"Device categories are unsupported: {', '.join(sorted(unknown))}")
    selected = tuple(profile for profile in profiles if not requested or profile.category in requested)
    return {
        "schema": DEVICE_MATRIX_SCHEMA,
        "status": "READY_FOR_EXECUTION",
        "profiles": [asdict(profile) for profile in selected],
        "profileCount": len(selected),
        "requiredMeasurements": [
            "screenshotSha256",
            "overflowCount",
            "clippedCount",
            "consoleErrors",
            "pageErrors",
            "interactionFailures",
            "accessibilityCritical",
            "elapsedMilliseconds",
        ],
        "executionAuthority": False,
        "providerCalls": 0,
    }


def evaluate_device_matrix(
    matrix: Mapping[str, object],
    measurements: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    raw_profiles = matrix.get("profiles")
    profiles = [item for item in raw_profiles if isinstance(item, dict)] if isinstance(raw_profiles, list) else []
    expected = {str(item.get("profile_id") or "") for item in profiles}
    rows_by_id = {str(item.get("profileId") or ""): item for item in measurements}
    missing = sorted(expected.difference(rows_by_id))
    unexpected = sorted(set(rows_by_id).difference(expected))
    results: list[dict[str, object]] = []
    for profile_id in sorted(expected.intersection(rows_by_id)):
        row = rows_by_id[profile_id]
        digest = str(row.get("screenshotSha256") or "")
        gates = {
            "screenshotPresent": len(digest) == 64 and all(character in "0123456789abcdefABCDEF" for character in digest),
            "noOverflow": int(row.get("overflowCount") or 0) == 0,
            "noClipping": int(row.get("clippedCount") or 0) == 0,
            "noConsoleErrors": int(row.get("consoleErrors") or 0) == 0,
            "noPageErrors": int(row.get("pageErrors") or 0) == 0,
            "interactionsPassed": int(row.get("interactionFailures") or 0) == 0,
            "accessibilityPassed": int(row.get("accessibilityCritical") or 0) == 0,
            "timed": float(row.get("elapsedMilliseconds") or 0) > 0,
        }
        results.append({"profileId": profile_id, "status": "PASS" if all(gates.values()) else "FAIL", "gates": gates})
    complete = bool(expected) and not missing and not unexpected and len(rows_by_id) == len(expected)
    return {
        "schema": "obtuse.blunder.device-matrix-evaluation.v1",
        "status": "PASS" if complete and results and all(item["status"] == "PASS" for item in results) else "FAIL",
        "profileCount": len(expected),
        "measuredProfiles": len(results),
        "missingProfiles": missing,
        "unexpectedProfiles": unexpected,
        "results": results,
        "sourceMutation": False,
    }


def write_device_matrix(path: Path, matrix: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(matrix), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
