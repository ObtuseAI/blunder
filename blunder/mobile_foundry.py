from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping


MOBILE_SCHEMA = "obtuse.blunder.mobile-foundry.v1"
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9_-]+){1,}$")


def read_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read mobile project manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Mobile project manifest must be an object: {path}")
    return value


def package_data(root: Path) -> dict[str, object]:
    path = root / "package.json"
    return read_json_object(path) if path.is_file() else {}


def dependency_names(package: Mapping[str, object]) -> frozenset[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        raw = package.get(key)
        if isinstance(raw, dict):
            names.update(str(item) for item in raw)
    return frozenset(names)


def detect_framework(root: Path, package: Mapping[str, object]) -> str | None:
    dependencies = dependency_names(package)
    if "expo" in dependencies or (root / "app.json").is_file() and "expo" in read_json_object(root / "app.json"):
        return "expo-react-native"
    if "react-native" in dependencies:
        return "react-native"
    if (root / "pubspec.yaml").is_file() and "flutter:" in (root / "pubspec.yaml").read_text(encoding="utf-8", errors="replace"):
        return "flutter"
    if (root / "android" / "settings.gradle").is_file() or (root / "android" / "settings.gradle.kts").is_file():
        return "native-android"
    if any(root.glob("ios/*.xcodeproj")):
        return "native-ios"
    return None


def expo_identifiers(root: Path) -> tuple[str | None, str | None]:
    path = root / "app.json"
    if not path.is_file():
        return None, None
    value = read_json_object(path)
    expo = value.get("expo")
    config = expo if isinstance(expo, dict) else {}
    ios = config.get("ios")
    android = config.get("android")
    return (
        str(ios.get("bundleIdentifier") or "") or None if isinstance(ios, dict) else None,
        str(android.get("package") or "") or None if isinstance(android, dict) else None,
    )


def native_android_identifier(root: Path) -> str | None:
    candidates = tuple(root.glob("android/app/build.gradle*"))
    for path in candidates:
        match = re.search(r"\bapplicationId\s*[=(]?\s*[\"']([^\"']+)", path.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1)
    return None


def native_ios_identifier(root: Path) -> str | None:
    for path in root.glob("ios/*.xcodeproj/project.pbxproj"):
        match = re.search(r"PRODUCT_BUNDLE_IDENTIFIER\s*=\s*([^;\s]+)", path.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1).strip('"')
    return None


def validation_commands(framework: str) -> list[dict[str, object]]:
    commands: dict[str, list[tuple[str, tuple[str, ...]]]] = {
        "expo-react-native": [
            ("typecheck", ("npx", "tsc", "--noEmit")),
            ("expo-doctor", ("npx", "expo-doctor")),
            ("preview-android", ("eas", "build", "--platform", "android", "--profile", "preview", "--non-interactive")),
            ("preview-ios", ("eas", "build", "--platform", "ios", "--profile", "preview", "--non-interactive")),
        ],
        "react-native": [
            ("typecheck", ("npx", "tsc", "--noEmit")),
            ("android-debug", ("gradlew", "assembleDebug")),
            ("ios-simulator", ("xcodebuild", "-configuration", "Debug", "-sdk", "iphonesimulator")),
        ],
        "flutter": [
            ("analyze", ("flutter", "analyze")),
            ("test", ("flutter", "test")),
            ("android-debug", ("flutter", "build", "apk", "--debug")),
        ],
        "native-android": [("android-test", ("gradlew", "test")), ("android-debug", ("gradlew", "assembleDebug"))],
        "native-ios": [("ios-test", ("xcodebuild", "test", "-sdk", "iphonesimulator"))],
    }
    external_effects = frozenset({"preview-android", "preview-ios"})
    return [
        {
            "id": identifier,
            "argv": list(argv),
            "executionAuthorized": False,
            "externalEffect": identifier in external_effects,
            "credentialAccess": identifier in external_effects,
        }
        for identifier, argv in commands[framework]
    ]


def mobile_project_report(root: Path) -> dict[str, object]:
    resolved = root.resolve()
    package = package_data(resolved)
    framework = detect_framework(resolved, package)
    if framework is None:
        return {
            "schema": MOBILE_SCHEMA,
            "status": "NOT_A_MOBILE_PROJECT",
            "framework": None,
            "sourceMutation": False,
            "externalExecution": False,
        }
    expo_ios, expo_android = expo_identifiers(resolved)
    ios_identifier = expo_ios or native_ios_identifier(resolved)
    android_identifier = expo_android or native_android_identifier(resolved)
    platform_candidates = {
        "ios": framework in {"expo-react-native", "react-native", "flutter", "native-ios"},
        "android": framework in {"expo-react-native", "react-native", "flutter", "native-android"},
    }
    platforms = [name for name, present in platform_candidates.items() if present]
    identifiers = {"ios": ios_identifier, "android": android_identifier}
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    gates = {
        "frameworkDetected": True,
        "platformDetected": bool(platforms),
        "identifiersValid": all(
            identifier is not None and IDENTIFIER.fullmatch(identifier) is not None
            for platform, identifier in identifiers.items()
            if platform in platforms
        ),
        "testSurfacePresent": bool(scripts.get("test")) or any(resolved.glob("test/**/*")) or any(resolved.glob("**/*Test.*")),
        "storeCredentialsAbsentFromSource": not any(resolved.glob("**/export_credentials.cfg")) and not any(resolved.glob("**/*.p8")),
    }
    return {
        "schema": MOBILE_SCHEMA,
        "status": "READY_FOR_EXECUTABLE_PROOF" if all(gates.values()) else "BLOCKED_STATIC_GATES",
        "framework": framework,
        "platforms": platforms,
        "identifiers": identifiers,
        "gates": gates,
        "commands": validation_commands(framework),
        "deviceProofRequired": True,
        "storeSubmissionAuthorized": False,
        "credentialMutationAuthorized": False,
        "sourceMutation": False,
        "providerCalls": 0,
    }
