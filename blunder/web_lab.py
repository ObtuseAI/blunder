from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping


WEB_SCHEMA = "obtuse.blunder.web-production-lab.v1"


def read_package(root: Path) -> dict[str, object]:
    path = root / "package.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read web package manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Web package manifest must be an object.")
    return value


def dependencies(package: Mapping[str, object]) -> frozenset[str]:
    result: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        raw = package.get(key)
        if isinstance(raw, dict):
            result.update(str(item) for item in raw)
    return frozenset(result)


def detect_web_framework(root: Path, package: Mapping[str, object]) -> str | None:
    observed = dependencies(package)
    for dependency, framework in (
        ("next", "next-app-router"),
        ("astro", "astro"),
        ("@sveltejs/kit", "sveltekit"),
        ("nuxt", "nuxt"),
        ("vite", "vite"),
    ):
        if dependency in observed:
            return framework
    return "static-html" if (root / "index.html").is_file() else None


def text_files(root: Path, suffixes: frozenset[str]) -> tuple[Path, ...]:
    blocked = frozenset({".git", "node_modules", "dist", "build", ".next"})
    return tuple(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in suffixes
        and not set(path.relative_to(root).parts).intersection(blocked)
        and path.stat().st_size <= 512 * 1024
    )


def joined_source(paths: tuple[Path, ...]) -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in paths)


def route_count(root: Path, framework: str) -> int:
    if framework == "next-app-router":
        return sum(1 for path in root.rglob("page.*") if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"})
    if framework == "static-html":
        return len(tuple(root.rglob("*.html")))
    return sum(1 for path in root.rglob("*.*") if path.stem in {"index", "+page"} and "node_modules" not in path.parts)


def web_commands(framework: str, scripts: Mapping[str, object]) -> list[dict[str, object]]:
    command_ids = tuple(identifier for identifier in ("lint", "typecheck", "test", "build") if identifier in scripts)
    commands = [
        {"id": identifier, "argv": ["npm", "run", identifier], "executionAuthorized": False, "externalEffect": False}
        for identifier in command_ids
    ]
    commands.append(
        {
            "id": "device-matrix",
            "argv": ["blunder", "product", "device-matrix"],
            "executionAuthorized": False,
            "externalEffect": False,
        }
    )
    return commands


def web_production_report(root: Path) -> dict[str, object]:
    resolved = root.resolve()
    package = read_package(resolved)
    framework = detect_web_framework(resolved, package)
    if framework is None:
        return {"schema": WEB_SCHEMA, "status": "NOT_A_WEB_PROJECT", "framework": None, "sourceMutation": False}
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    sources = text_files(resolved, frozenset({".html", ".js", ".jsx", ".ts", ".tsx", ".css"}))
    content = joined_source(sources).lower()
    html_content = joined_source(tuple(path for path in sources if path.suffix.lower() == ".html")).lower()
    metadata_present = bool(
        re.search(r"<title\b|export\s+const\s+metadata|generateMetadata", content, re.IGNORECASE)
    )
    semantic_main = "<main" in html_content or "<main" in content
    language_declared = bool(re.search(r"<html[^>]+lang=", html_content, re.IGNORECASE)) or framework != "static-html"
    responsive = "@media" in content or "viewport" in html_content or framework != "static-html"
    csp = "content-security-policy" in content or "contentsecuritypolicy" in content
    gates = {
        "frameworkDetected": True,
        "routesPresent": route_count(resolved, framework) > 0,
        "productionBuildScript": bool(scripts.get("build")) if framework != "static-html" else True,
        "metadataPresent": metadata_present,
        "semanticMainPresent": semantic_main,
        "documentLanguageDeclared": language_declared,
        "responsiveContractPresent": responsive,
        "automatedTestsPresent": bool(scripts.get("test")) or any(resolved.glob("**/*.spec.*")) or any(resolved.glob("**/*.test.*")),
        "robotsOrExplicitPrivatePolicy": (resolved / "public" / "robots.txt").is_file() or (resolved / "robots.txt").is_file() or "robots" in content,
        "contentSecurityPolicyPresent": csp,
    }
    minimum_gates = (
        "frameworkDetected",
        "routesPresent",
        "productionBuildScript",
        "metadataPresent",
        "semanticMainPresent",
        "documentLanguageDeclared",
        "responsiveContractPresent",
        "automatedTestsPresent",
    )
    return {
        "schema": WEB_SCHEMA,
        "status": "READY_FOR_EXECUTABLE_PROOF" if all(gates[key] for key in minimum_gates) else "BLOCKED_STATIC_GATES",
        "framework": framework,
        "routeCount": route_count(resolved, framework),
        "sourceFilesAnalyzed": len(sources),
        "gates": gates,
        "qualityBudgets": {"lcpMilliseconds": 2500, "inpMilliseconds": 200, "cls": 0.1, "accessibilityCritical": 0},
        "commands": web_commands(framework, scripts),
        "deploymentAuthorized": False,
        "sourceMutation": False,
        "providerCalls": 0,
    }
