from __future__ import annotations


COMPATIBLE_LICENSES: set[str] = {
    "INTERNAL",
    "OWNED",
    "OFFICIAL_REFERENCE",
    "MIT",
    "APACHE-2.0",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "PUBLIC_DOMAIN",
}

INCOMPATIBLE_MARKERS: list[str] = [
    "no redistribution",
    "proprietary confidential",
    "all rights reserved incompatible",
    "license incompatible",
]


def classify_license(text: str, declared_license: str) -> str:
    lowered = text.lower()
    for marker in INCOMPATIBLE_MARKERS:
        if marker in lowered:
            return "INCOMPATIBLE"
    if declared_license:
        return declared_license.upper()
    return "UNKNOWN"


def license_score(license_class: str) -> float:
    normalized = license_class.upper()
    if normalized in COMPATIBLE_LICENSES:
        return 1.0
    if normalized == "UNKNOWN":
        return 0.35
    return -2.0


def is_license_compatible(license_class: str) -> bool:
    return license_score(license_class) > 0
