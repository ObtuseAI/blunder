"""Strict effect-surface registry and static adapter coverage audit."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, cast

from blunder.constitutional_kernel import canonical_sha256, load_runtime_constitution, load_strict_json


EnforcementMode = Literal["SHADOW", "FAIL_CLOSED"]
RiskClass = Literal["LOWER_RISK", "HIGH_RISK"]
REGISTRY_SCHEMA = "blunder.effect-surface-registry.v1"
_HIGH_RISK_EFFECTS: frozenset[str] = frozenset(
    {
        "provider.call",
        "source.mutate",
        "network.external",
        "mission.integrate",
        "mission.cleanup",
        "git.push",
        "release.publish",
        "deployment.production",
        "benchmark.live-launch",
        "policy.root-mutate",
        "credentials.reveal",
        "filesystem.destructive-cleanup",
        "permission.bypass",
    }
)


@dataclass(frozen=True)
class EffectSurface:
    surface_id: str
    module: str
    symbol: str
    effects: tuple[str, ...]
    adapter: str
    enforcement: EnforcementMode
    risk_class: RiskClass


@dataclass(frozen=True)
class SinkExpectation:
    module: str
    symbol: str
    sink: str
    occurrences: int


@dataclass(frozen=True)
class EffectSurfaceRegistry:
    schema: str
    source_revision: str
    covered_modules: tuple[str, ...]
    monitored_sinks: tuple[str, ...]
    sink_expectations: tuple[SinkExpectation, ...]
    surfaces: tuple[EffectSurface, ...]
    digest: str


def _exact_keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{context} keys are invalid; missing={sorted(expected - actual)}; unknown={sorted(actual - expected)}"
        )


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{context} must be a non-empty string")
    return value


def _strings(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be an array")
    parsed = tuple(_string(item, f"{context}[{index}]") for index, item in enumerate(value))
    if not parsed or len(parsed) != len(set(parsed)):
        raise ValueError(f"{context} must contain unique values")
    return tuple(sorted(parsed))


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be an object")
    return cast(Mapping[str, object], value)


def _objects(value: object, context: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} must be a non-empty array")
    return tuple(_mapping(item, f"{context}[{index}]") for index, item in enumerate(value))


def parse_effect_surface_registry(value: Mapping[str, object]) -> EffectSurfaceRegistry:
    """Strictly parse the package-owned effect-surface registry."""

    _exact_keys(
        value,
        {
            "schema",
            "sourceRevision",
            "coveredModules",
            "monitoredSinks",
            "sinkExpectations",
            "surfaces",
        },
        "effectRegistry",
    )
    if value["schema"] != REGISTRY_SCHEMA:
        raise ValueError(f"effectRegistry.schema is unsupported: {value['schema']}")
    expectations: list[SinkExpectation] = []
    for index, raw in enumerate(_objects(value["sinkExpectations"], "effectRegistry.sinkExpectations")):
        context = f"effectRegistry.sinkExpectations[{index}]"
        _exact_keys(raw, {"module", "symbol", "sink", "occurrences"}, context)
        occurrences = raw["occurrences"]
        if not isinstance(occurrences, int) or isinstance(occurrences, bool) or occurrences < 1:
            raise ValueError(f"{context}.occurrences must be a positive integer")
        expectations.append(
            SinkExpectation(
                _string(raw["module"], f"{context}.module"),
                _string(raw["symbol"], f"{context}.symbol"),
                _string(raw["sink"], f"{context}.sink"),
                occurrences,
            )
        )
    surfaces: list[EffectSurface] = []
    for index, raw in enumerate(_objects(value["surfaces"], "effectRegistry.surfaces")):
        context = f"effectRegistry.surfaces[{index}]"
        _exact_keys(
            raw,
            {"surfaceId", "module", "symbol", "effects", "adapter", "enforcement", "riskClass"},
            context,
        )
        enforcement = raw["enforcement"]
        risk_class = raw["riskClass"]
        if enforcement not in {"SHADOW", "FAIL_CLOSED"}:
            raise ValueError(f"{context}.enforcement is unsupported: {enforcement}")
        if risk_class not in {"LOWER_RISK", "HIGH_RISK"}:
            raise ValueError(f"{context}.riskClass is unsupported: {risk_class}")
        surfaces.append(
            EffectSurface(
                _string(raw["surfaceId"], f"{context}.surfaceId"),
                _string(raw["module"], f"{context}.module"),
                _string(raw["symbol"], f"{context}.symbol"),
                _strings(raw["effects"], f"{context}.effects"),
                _string(raw["adapter"], f"{context}.adapter"),
                cast(EnforcementMode, enforcement),
                cast(RiskClass, risk_class),
            )
        )
    registry = EffectSurfaceRegistry(
        REGISTRY_SCHEMA,
        _string(value["sourceRevision"], "effectRegistry.sourceRevision"),
        _strings(value["coveredModules"], "effectRegistry.coveredModules"),
        _strings(value["monitoredSinks"], "effectRegistry.monitoredSinks"),
        tuple(expectations),
        tuple(surfaces),
        canonical_sha256(value),
    )
    surface_ids = [surface.surface_id for surface in registry.surfaces]
    if len(surface_ids) != len(set(surface_ids)):
        raise ValueError("effectRegistry.surfaces must use unique surfaceId values")
    expectation_keys = [(item.module, item.symbol, item.sink) for item in registry.sink_expectations]
    if len(expectation_keys) != len(set(expectation_keys)):
        raise ValueError("effectRegistry.sinkExpectations must use unique module, symbol, and sink tuples")
    return registry


def load_effect_surface_registry() -> EffectSurfaceRegistry:
    """Load the package-owned effect registry without an implicit fallback."""

    path = Path(__file__).resolve().parent / "config" / "effect-surfaces.v1.json"
    return parse_effect_surface_registry(load_strict_json(path))


def _module_path(root: Path, module: str) -> Path:
    return root.resolve() / Path(*module.split(".")).with_suffix(".py")


def _qualified_call(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_call(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _sink_name(call_name: str, monitored_sinks: frozenset[str]) -> str | None:
    if call_name in monitored_sinks:
        return call_name
    attribute = call_name.rsplit(".", 1)[-1]
    generic = f"path.{attribute}"
    return generic if generic in monitored_sinks else None


def _discover_sinks(path: Path, monitored_sinks: frozenset[str]) -> dict[tuple[str, str], int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    counts: dict[tuple[str, str], int] = {}
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            sink = _sink_name(_qualified_call(node.func), monitored_sinks)
            if sink is not None:
                symbol = ".".join(stack) if stack else "<module>"
                key = (symbol, sink)
                counts[key] = counts.get(key, 0) + 1
            self.generic_visit(node)

    Visitor().visit(tree)
    return counts


def audit_effect_surface_coverage(root: Path) -> dict[str, object]:
    """Fail closed when covered source sinks or adapter registrations drift."""

    registry = load_effect_surface_registry()
    constitution = load_runtime_constitution()
    errors: list[str] = []
    if registry.source_revision != constitution.source_revision:
        errors.append(
            "registry source revision does not match the constitution: "
            f"registry={registry.source_revision}; constitution={constitution.source_revision}"
        )
    covered_modules = set(registry.covered_modules)
    expected: dict[str, dict[tuple[str, str], int]] = {module: {} for module in registry.covered_modules}
    for expectation in registry.sink_expectations:
        if expectation.module not in covered_modules:
            errors.append(f"sink expectation references an uncovered module: {expectation.module}")
            continue
        expected[expectation.module][(expectation.symbol, expectation.sink)] = expectation.occurrences
    actual: dict[str, dict[tuple[str, str], int]] = {}
    source_by_module: dict[str, str] = {}
    monitored = frozenset(registry.monitored_sinks)
    for module in registry.covered_modules:
        path = _module_path(root, module)
        if not path.is_file():
            errors.append(f"covered effect module is missing: {path}")
            continue
        source_by_module[module] = path.read_text(encoding="utf-8")
        actual[module] = _discover_sinks(path, monitored)
        if actual[module] != expected[module]:
            errors.append(
                f"effect sink inventory drifted for {module}: expected={expected[module]}; actual={actual[module]}"
            )
    registered_symbols = {(surface.module, surface.symbol) for surface in registry.surfaces}
    for expectation in registry.sink_expectations:
        if (expectation.module, expectation.symbol) not in registered_symbols:
            errors.append(
                "effect sink lacks a registered enforcement surface: "
                f"module={expectation.module}; symbol={expectation.symbol}; sink={expectation.sink}"
            )
    for surface in registry.surfaces:
        unknown_effects = sorted(set(surface.effects) - set(constitution.effects))
        if unknown_effects:
            errors.append(f"surface {surface.surface_id} references unknown effects: {unknown_effects}")
        if set(surface.effects).intersection(_HIGH_RISK_EFFECTS) and surface.enforcement != "FAIL_CLOSED":
            errors.append(f"high-risk surface is not fail closed: {surface.surface_id}")
        if surface.risk_class == "HIGH_RISK" and surface.enforcement != "FAIL_CLOSED":
            errors.append(f"high-risk classification is not fail closed: {surface.surface_id}")
        source = source_by_module.get(surface.module)
        if source is None:
            continue
        if surface.adapter not in source:
            errors.append(
                f"surface adapter is not referenced by its module: surface={surface.surface_id}; adapter={surface.adapter}"
            )
    return {
        "schema": "blunder.effect-surface-coverage-report.v1",
        "status": "PASS" if not errors else "FAIL",
        "sourceRevision": registry.source_revision,
        "constitutionDigest": constitution.digest,
        "registryDigest": registry.digest,
        "coveredModuleCount": len(registry.covered_modules),
        "surfaceCount": len(registry.surfaces),
        "sinkExpectationCount": len(registry.sink_expectations),
        "highRiskSurfaceCount": sum(surface.risk_class == "HIGH_RISK" for surface in registry.surfaces),
        "errors": errors,
    }
