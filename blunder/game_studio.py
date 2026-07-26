from __future__ import annotations

import json
import re
from pathlib import Path


GAME_SCHEMA = "obtuse.blunder.game-studio.v1"


def detect_game_engine(root: Path) -> str | None:
    if (root / "project.godot").is_file():
        return "godot"
    if (root / "ProjectSettings" / "ProjectVersion.txt").is_file():
        return "unity"
    if any(root.glob("*.uproject")):
        return "unreal"
    package = root / "package.json"
    if package.is_file():
        try:
            value = json.loads(package.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Cannot read game package manifest: {error}") from error
        dependencies = value.get("dependencies") if isinstance(value, dict) else None
        names = frozenset(str(item) for item in dependencies) if isinstance(dependencies, dict) else frozenset()
        if names.intersection({"phaser", "three", "pixi.js", "@babylonjs/core"}):
            return "web-game"
    return None


def source_inventory(root: Path) -> dict[str, int]:
    suffix_groups = {
        "scenes": frozenset({".tscn", ".unity", ".umap"}),
        "scripts": frozenset({".gd", ".cs", ".cpp", ".ts", ".js"}),
        "textures": frozenset({".png", ".jpg", ".jpeg", ".webp"}),
        "audio": frozenset({".wav", ".ogg", ".mp3"}),
        "models": frozenset({".gltf", ".glb", ".fbx", ".obj"}),
    }
    blocked = frozenset({".git", ".godot", "Library", "Temp", "node_modules", "Binaries", "Intermediate"})
    files = tuple(path for path in root.rglob("*") if path.is_file() and not set(path.relative_to(root).parts).intersection(blocked))
    return {name: sum(1 for path in files if path.suffix.lower() in suffixes) for name, suffixes in suffix_groups.items()}


def engine_evidence(root: Path, engine: str) -> dict[str, object]:
    if engine == "godot":
        project = (root / "project.godot").read_text(encoding="utf-8", errors="replace")
        return {
            "mainScene": bool(re.search(r"^run/main_scene\s*=", project, re.MULTILINE)),
            "inputMap": "[input]" in project,
            "exportConfiguration": (root / "export_presets.cfg").is_file(),
            "headlessCommand": ["godot", "--headless", "--path", ".", "--quit-after", "1"],
        }
    if engine == "unity":
        return {
            "mainScene": (root / "ProjectSettings" / "EditorBuildSettings.asset").is_file(),
            "inputMap": (root / "ProjectSettings" / "InputManager.asset").is_file(),
            "exportConfiguration": (root / "ProjectSettings" / "ProjectVersion.txt").is_file(),
            "headlessCommand": ["Unity", "-batchmode", "-projectPath", ".", "-runTests", "-quit"],
        }
    if engine == "unreal":
        return {
            "mainScene": any(root.glob("Config/DefaultEngine.ini")),
            "inputMap": any(root.glob("Config/DefaultInput.ini")),
            "exportConfiguration": any(root.glob("*.uproject")),
            "headlessCommand": ["UnrealEditor-Cmd", "project.uproject", "-unattended", "-ExecCmds=Automation RunTests Project"],
        }
    return {
        "mainScene": any(root.glob("src/**/*.*")),
        "inputMap": True,
        "exportConfiguration": (root / "package.json").is_file(),
        "headlessCommand": ["npm", "test"],
    }


def game_studio_report(root: Path) -> dict[str, object]:
    resolved = root.resolve()
    engine = detect_game_engine(resolved)
    if engine is None:
        return {"schema": GAME_SCHEMA, "status": "NOT_A_GAME_PROJECT", "engine": None, "sourceMutation": False}
    inventory = source_inventory(resolved)
    evidence = engine_evidence(resolved, engine)
    tests = tuple(resolved.glob("**/test*.*")) + tuple(resolved.glob("**/*Test.*"))
    replay = tuple(resolved.glob("**/*replay*.*"))
    gates = {
        "engineDetected": True,
        "scenePresent": inventory["scenes"] > 0 or evidence["mainScene"] is True,
        "mainSceneConfigured": evidence["mainScene"] is True,
        "inputMapConfigured": evidence["inputMap"] is True,
        "automatedTestSurface": bool(tests),
        "deterministicReplaySurface": bool(replay),
        "exportConfiguration": evidence["exportConfiguration"] is True,
    }
    return {
        "schema": GAME_SCHEMA,
        "status": "READY_FOR_HEADLESS_PROOF" if all(gates.values()) else "BLOCKED_STATIC_GATES",
        "engine": engine,
        "inventory": inventory,
        "gates": gates,
        "qualityBudgets": {
            "targetFramesPerSecond": 60,
            "p95FrameMilliseconds": 16.67,
            "maximumCrashCount": 0,
            "maximumReplayDivergence": 0,
            "maximumInputLatencyMilliseconds": 100,
        },
        "commands": [
            {
                "id": "headless-validation",
                "argv": evidence["headlessCommand"],
                "executionAuthorized": False,
                "externalEffect": False,
            }
        ],
        "exportAuthorized": False,
        "sourceMutation": False,
        "providerCalls": 0,
    }
