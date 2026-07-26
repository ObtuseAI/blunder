from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from blunder.cognition import normalized_terms


PLUGIN_SCHEMA = "obtuse.blunder.capability-plugin.v1"
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
ALLOWED_ACTIONS: frozenset[str] = frozenset({"status", "evolve", "planning_gate", "tests", "readiness"})
ALLOWED_HOOK_EVENTS: frozenset[str] = frozenset(
    {"mission.completed", "task.briefed", "checkpoint.restored"}
)
ALLOWED_HOOK_ACTIONS: frozenset[str] = frozenset({"audit.record"})
ALLOWED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "artifact:read",
        "artifact:write",
        "checkpoint:read",
        "checkpoint:restore",
        "evolution:plan",
        "provider:bounded",
        "queue:read",
        "runtime:read",
        "source:read",
        "source:search",
        "source:write-bounded",
        "tests:run",
    }
)


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    name: str
    description: str
    permissions: tuple[str, ...]
    action_id: str | None
    mutates_source: bool
    provider_required: bool


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    name: str
    description: str
    triggers: tuple[str, ...]
    workflow: tuple[str, ...]
    tool_ids: tuple[str, ...]
    context_mode: str
    side_effects: bool


@dataclass(frozen=True)
class HookSpec:
    hook_id: str
    event: str
    action: str
    description: str


@dataclass(frozen=True)
class PluginSpec:
    plugin_id: str
    name: str
    version: str
    description: str
    manifest_hash: str
    skills: tuple[SkillSpec, ...]
    tools: tuple[ToolSpec, ...]
    hooks: tuple[HookSpec, ...]


@dataclass(frozen=True)
class CapabilityRegistry:
    plugins: tuple[PluginSpec, ...]
    skills: tuple[SkillSpec, ...]
    tools: tuple[ToolSpec, ...]
    hooks: tuple[HookSpec, ...]


def require_string(value: Mapping[str, object], key: str, context: str) -> str:
    result = str(value.get(key) or "").strip()
    if not result:
        raise ValueError(f"{context} requires a non-empty {key}.")
    return result


def require_identifier(value: str, context: str) -> str:
    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{context} has an invalid identifier: {value}")
    return value


def string_tuple(value: object, key: str, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} requires a non-empty {key} list.")
    items = tuple(str(item).strip() for item in value)
    if any(not item for item in items):
        raise ValueError(f"{context} {key} contains an empty value.")
    return items


def parse_tool(value: Mapping[str, object], plugin_id: str) -> ToolSpec:
    context = f"plugin {plugin_id} tool"
    local_id = require_identifier(require_string(value, "id", context), context)
    permissions = string_tuple(value.get("permissions"), "permissions", context)
    unknown = set(permissions).difference(ALLOWED_PERMISSIONS)
    if unknown:
        raise ValueError(f"{context} requests unknown permissions: {', '.join(sorted(unknown))}")
    action_value = value.get("actionId")
    action_id = str(action_value).strip() if action_value is not None else None
    if action_id and action_id not in ALLOWED_ACTIONS:
        raise ValueError(f"{context} requests an unapproved action: {action_id}")
    mutates_source = value.get("mutatesSource") is True
    if mutates_source and "source:write-bounded" not in permissions:
        raise ValueError(f"{context} mutates source without source:write-bounded permission.")
    return ToolSpec(
        tool_id=f"{plugin_id}:{local_id}",
        name=require_string(value, "name", context),
        description=require_string(value, "description", context),
        permissions=permissions,
        action_id=action_id,
        mutates_source=mutates_source,
        provider_required=value.get("providerRequired") is True,
    )


def parse_skill(value: Mapping[str, object], plugin_id: str, tool_ids: frozenset[str]) -> SkillSpec:
    context = f"plugin {plugin_id} skill"
    local_id = require_identifier(require_string(value, "id", context), context)
    requested_tools = string_tuple(value.get("tools"), "tools", context)
    qualified_tools = tuple(item if ":" in item else f"{plugin_id}:{item}" for item in requested_tools)
    missing = set(qualified_tools).difference(tool_ids)
    if missing:
        raise ValueError(f"{context} references missing tools: {', '.join(sorted(missing))}")
    context_mode = require_string(value, "contextMode", context)
    if context_mode not in {"on-demand", "isolated"}:
        raise ValueError(f"{context} has unsupported contextMode: {context_mode}")
    return SkillSpec(
        skill_id=f"{plugin_id}:{local_id}",
        name=require_string(value, "name", context),
        description=require_string(value, "description", context),
        triggers=string_tuple(value.get("triggers"), "triggers", context),
        workflow=string_tuple(value.get("workflow"), "workflow", context),
        tool_ids=qualified_tools,
        context_mode=context_mode,
        side_effects=value.get("sideEffects") is True,
    )


def parse_hook(value: Mapping[str, object], plugin_id: str) -> HookSpec:
    context = f"plugin {plugin_id} hook"
    local_id = require_identifier(require_string(value, "id", context), context)
    event = require_string(value, "event", context)
    action = require_string(value, "action", context)
    if event not in ALLOWED_HOOK_EVENTS:
        raise ValueError(f"{context} requests an unsupported event: {event}")
    if action not in ALLOWED_HOOK_ACTIONS:
        raise ValueError(f"{context} requests an unsupported action: {action}")
    return HookSpec(
        hook_id=f"{plugin_id}:{local_id}",
        event=event,
        action=action,
        description=require_string(value, "description", context),
    )


def manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def parse_plugin(path: Path) -> PluginSpec:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read capability plugin manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Capability plugin manifest must be an object: {path}")
    if value.get("schema") != PLUGIN_SCHEMA:
        raise ValueError(f"Capability plugin manifest has unsupported schema: {path}")
    if value.get("firstParty") is not True or value.get("enabled") is not True:
        raise ValueError(f"Capability plugin must be enabled and first-party: {path}")
    plugin_id = require_identifier(require_string(value, "id", str(path)), str(path))
    if path.parent.name != plugin_id:
        raise ValueError(f"Capability plugin directory must match its id: {plugin_id}")
    raw_tools = value.get("tools")
    raw_skills = value.get("skills")
    raw_hooks = value.get("hooks")
    if not isinstance(raw_tools, list) or not isinstance(raw_skills, list) or not isinstance(raw_hooks, list):
        raise ValueError(f"Capability plugin arrays are invalid: {path}")
    tools = tuple(parse_tool(item, plugin_id) for item in raw_tools if isinstance(item, dict))
    if len(tools) != len(raw_tools):
        raise ValueError(f"Capability plugin tools must all be objects: {path}")
    tool_ids = frozenset(tool.tool_id for tool in tools)
    skills = tuple(parse_skill(item, plugin_id, tool_ids) for item in raw_skills if isinstance(item, dict))
    if len(skills) != len(raw_skills):
        raise ValueError(f"Capability plugin skills must all be objects: {path}")
    hooks = tuple(parse_hook(item, plugin_id) for item in raw_hooks if isinstance(item, dict))
    if len(hooks) != len(raw_hooks):
        raise ValueError(f"Capability plugin hooks must all be objects: {path}")
    return PluginSpec(
        plugin_id=plugin_id,
        name=require_string(value, "name", str(path)),
        version=require_string(value, "version", str(path)),
        description=require_string(value, "description", str(path)),
        manifest_hash=manifest_sha256(path),
        skills=skills,
        tools=tools,
        hooks=hooks,
    )


def ensure_unique(values: Sequence[str], label: str) -> None:
    duplicates = sorted(value for value in set(values) if values.count(value) > 1)
    if duplicates:
        raise ValueError(f"Duplicate {label}: {', '.join(duplicates)}")


def load_registry(root: Path) -> CapabilityRegistry:
    base = (root / "blunder" / "capability_packs").resolve()
    if not base.is_dir():
        raise RuntimeError(f"Blunder capability pack root is missing: {base}")
    manifests = sorted(base.glob("*/manifest.json"))
    if not manifests:
        raise RuntimeError(f"Blunder has no first-party capability plugins: {base}")
    resolved_manifests = tuple(path.resolve() for path in manifests)
    if any(not path.is_relative_to(base) for path in resolved_manifests):
        raise ValueError("A capability plugin manifest escapes the first-party pack root.")
    plugins = tuple(parse_plugin(path) for path in resolved_manifests)
    skills = tuple(skill for plugin in plugins for skill in plugin.skills)
    tools = tuple(tool for plugin in plugins for tool in plugin.tools)
    hooks = tuple(hook for plugin in plugins for hook in plugin.hooks)
    ensure_unique(tuple(plugin.plugin_id for plugin in plugins), "plugin ids")
    ensure_unique(tuple(skill.skill_id for skill in skills), "skill ids")
    ensure_unique(tuple(tool.tool_id for tool in tools), "tool ids")
    ensure_unique(tuple(hook.hook_id for hook in hooks), "hook ids")
    return CapabilityRegistry(plugins=plugins, skills=skills, tools=tools, hooks=hooks)


def registry_public(registry: CapabilityRegistry) -> dict[str, object]:
    plugins = [
        {
            "plugin_id": plugin.plugin_id,
            "name": plugin.name,
            "version": plugin.version,
            "description": plugin.description,
            "manifest_hash": plugin.manifest_hash,
            "skills": [
                {
                    "skill_id": skill.skill_id,
                    "name": skill.name,
                    "description": skill.description,
                    "triggers": list(skill.triggers),
                    "context_mode": skill.context_mode,
                }
                for skill in plugin.skills
            ],
            "tools": [
                {
                    "tool_id": tool.tool_id,
                    "name": tool.name,
                    "description": tool.description,
                    "invocable": tool.action_id is not None,
                }
                for tool in plugin.tools
            ],
            "hooks": [
                {
                    "hook_id": hook.hook_id,
                    "event": hook.event,
                    "description": hook.description,
                }
                for hook in plugin.hooks
            ],
        }
        for plugin in registry.plugins
    ]
    return {
        "schema": "obtuse.blunder.capability-registry.v1",
        "counts": {
            "plugins": len(registry.plugins),
            "skills": len(registry.skills),
            "tools": len(registry.tools),
            "hooks": len(registry.hooks),
        },
        "plugins": plugins,
        "permissions": sorted(ALLOWED_PERMISSIONS),
        "externalPluginsAllowed": False,
        "arbitraryCommandsAllowed": False,
        "firstPartyOnly": True,
    }


def skill_score(skill: SkillSpec, terms: frozenset[str]) -> int:
    trigger_terms = normalized_terms(" ".join(skill.triggers))
    description_terms = normalized_terms(f"{skill.name} {skill.description}")
    return len(terms.intersection(trigger_terms)) * 5 + len(terms.intersection(description_terms)) * 2


def route_task_with_bias(
    registry: CapabilityRegistry,
    task: str,
    limit: int,
    skill_bias: Mapping[str, float],
) -> dict[str, object]:
    normalized = task.strip()
    if len(normalized) < 3:
        raise ValueError("Capability routing requires a concrete task.")
    terms = normalized_terms(normalized)
    ranked = sorted(
        ((skill_score(skill, terms) + float(skill_bias.get(skill.skill_id, 0.0)), skill) for skill in registry.skills),
        key=lambda item: (-item[0], item[1].skill_id),
    )
    top_score = ranked[0][0] if ranked else 0
    selection_floor = max(3, (top_score + 1) // 2)
    selected = [(score, skill) for score, skill in ranked if score >= selection_floor][:limit]
    if not selected:
        selected = ranked[:1]
    selected_tool_ids = tuple(dict.fromkeys(tool for _, skill in selected for tool in skill.tool_ids))
    tools_by_id = {tool.tool_id: tool for tool in registry.tools}
    selected_tools = tuple(tools_by_id[tool_id] for tool_id in selected_tool_ids)
    return {
        "schema": "obtuse.blunder.capability-route.v1",
        "task": normalized,
        "skills": [asdict(skill) | {"score": score} for score, skill in selected],
        "tools": [asdict(tool) for tool in selected_tools],
        "permissions": sorted({permission for tool in selected_tools for permission in tool.permissions}),
        "providerRequired": any(tool.provider_required for tool in selected_tools),
        "sourceMutationPossible": any(tool.mutates_source for tool in selected_tools),
        "externalEffectsAllowed": False,
        "outcomeLearningApplied": bool(skill_bias),
    }


def route_task(registry: CapabilityRegistry, task: str, limit: int) -> dict[str, object]:
    return route_task_with_bias(registry, task, limit, {})


def action_for_tool(registry: CapabilityRegistry, tool_id: str) -> str:
    matches = tuple(tool for tool in registry.tools if tool.tool_id == tool_id)
    if len(matches) != 1:
        raise ValueError(f"Capability tool does not exist: {tool_id}")
    action_id = matches[0].action_id
    if action_id is None:
        raise ValueError(f"Capability tool is an IDE-native context tool and has no background action: {tool_id}")
    return action_id


def dispatch_hooks(
    root: Path,
    registry: CapabilityRegistry,
    event: str,
    payload: Mapping[str, object],
) -> tuple[str, ...]:
    if event not in ALLOWED_HOOK_EVENTS:
        raise ValueError(f"Unsupported Blunder lifecycle event: {event}")
    matching = tuple(hook for hook in registry.hooks if hook.event == event)
    if not matching:
        return ()
    artifact = root / "artifacts" / "obtuse" / "blunder-capability-foundry-v1"
    artifact.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "obtuse.blunder.capability-hook-event.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "hooks": [hook.hook_id for hook in matching],
        "payload": dict(payload),
    }
    with (artifact / "hook-events.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
    return tuple(hook.hook_id for hook in matching)
