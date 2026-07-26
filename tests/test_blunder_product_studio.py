from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from blunder.app_blueprint import compile_blueprint
from blunder.asset_intelligence import asset_intelligence_report
from blunder.design_system import compile_design_system
from blunder.device_matrix import build_device_matrix, evaluate_device_matrix
from blunder.game_studio import game_studio_report
from blunder.mobile_foundry import mobile_project_report
from blunder.polyglot_intelligence import polyglot_index, polyglot_references
from blunder.product_arena import load_product_suite, parse_measurement, product_arena_report
from blunder.visual_judgment import visual_judgment_report
from blunder.web_lab import web_production_report


REPO_ROOT = Path(__file__).resolve().parents[1]


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def rgba_png(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    scanline = bytes(color) * width
    pixels = b"".join(b"\x00" + scanline for _ in range(height))
    return signature + png_chunk(b"IHDR", header) + png_chunk(b"IDAT", zlib.compress(pixels)) + png_chunk(b"IEND", b"")


def blueprint_request() -> dict[str, object]:
    return {
        "productName": "Atlas",
        "goal": "Help independent teams plan and complete resilient product launches.",
        "audience": ["product operator", "reviewer"],
        "platforms": ["web", "ios", "android"],
        "capabilities": ["create a launch plan", "review readiness evidence"],
        "entities": ["Launch", "Evidence"],
        "constraints": ["private by default"],
    }


def passing_report(status: str) -> dict[str, object]:
    return {"status": status}


class BlunderProductStudioIntegrationTests(unittest.TestCase):
    def test_p21_blueprint_compiles_journeys_states_budgets_and_authority(self) -> None:
        report = compile_blueprint(blueprint_request())
        self.assertEqual(report["status"], "READY_FOR_PRODUCT_PLANNING")
        self.assertEqual(report["domain"], "mobile")
        self.assertEqual(len(report["surfaces"]), 2)
        self.assertIn("permission-denied", report["surfaces"][0]["states"])
        self.assertFalse(report["authority"]["deployment"])

    def test_p22_visual_judgment_reads_real_png_and_enforces_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "baseline.png").write_bytes(rgba_png(4, 3, (10, 20, 30, 255)))
            (root / "candidate.png").write_bytes(rgba_png(4, 3, (10, 20, 30, 255)))
            report = visual_judgment_report(
                root,
                (
                    {
                        "id": "home-phone",
                        "baseline": "baseline.png",
                        "candidate": "candidate.png",
                        "pixelThreshold": 0,
                        "maximumChangedPercent": 0,
                        "maximumMeanChannelDelta": 0,
                        "layout": {"overflowCount": 0, "clippedCount": 0, "focusVisible": True, "consoleErrors": 0},
                    },
                ),
            )
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["cases"][0]["difference"]["changedPixels"], 0)

    def test_p23_device_matrix_requires_complete_real_measurements(self) -> None:
        matrix = build_device_matrix("en-US", ())
        measurements = [
            {
                "profileId": item["profile_id"],
                "screenshotSha256": "A" * 64,
                "overflowCount": 0,
                "clippedCount": 0,
                "consoleErrors": 0,
                "pageErrors": 0,
                "interactionFailures": 0,
                "accessibilityCritical": 0,
                "elapsedMilliseconds": 100,
            }
            for item in matrix["profiles"]
        ]
        report = evaluate_device_matrix(matrix, measurements)
        self.assertEqual(matrix["profileCount"], 8)
        self.assertEqual(report["status"], "PASS")

    def test_p24_mobile_foundry_recognizes_release_ready_expo_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"dependencies": {"expo": "1"}, "scripts": {"test": "node test.js"}}), encoding="utf-8")
            (root / "app.json").write_text(json.dumps({"expo": {"ios": {"bundleIdentifier": "ai.obtuse.atlas"}, "android": {"package": "ai.obtuse.atlas"}}}), encoding="utf-8")
            (root / "test.js").write_text("export const pass = true;\n", encoding="utf-8")
            report = mobile_project_report(root)
            self.assertEqual(report["status"], "READY_FOR_EXECUTABLE_PROOF")
            self.assertEqual(report["framework"], "expo-react-native")
            self.assertFalse(report["storeSubmissionAuthorized"])

    def test_p25_web_lab_checks_semantics_responsiveness_tests_and_production(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text(
                '<!doctype html><html lang="en"><head><title>Atlas</title><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src self"><style>@media(max-width:600px){main{display:block}}</style></head><body><main>Ready</main></body></html>',
                encoding="utf-8",
            )
            (root / "index.test.js").write_text("export const tested = true;\n", encoding="utf-8")
            (root / "robots.txt").write_text("User-agent: *\nDisallow:\n", encoding="utf-8")
            report = web_production_report(root)
            self.assertEqual(report["status"], "READY_FOR_EXECUTABLE_PROOF")
            self.assertTrue(all(report["gates"].values()))

    def test_p26_game_studio_requires_replay_headless_and_export_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project.godot").write_text('[application]\nrun/main_scene="res://main.tscn"\n[input]\njump={}\n', encoding="utf-8")
            (root / "main.tscn").write_text("[gd_scene]\n", encoding="utf-8")
            (root / "export_presets.cfg").write_text("[preset.0]\nname=\"Windows\"\n", encoding="utf-8")
            (root / "test_game.gd").write_text("func test_loop():\n    pass\n", encoding="utf-8")
            (root / "deterministic_replay.json").write_text("{}\n", encoding="utf-8")
            report = game_studio_report(root)
            self.assertEqual(report["status"], "READY_FOR_HEADLESS_PROOF")
            self.assertFalse(report["exportAuthorized"])

    def test_p27_design_system_compiles_accessible_tokens_and_css(self) -> None:
        report = compile_design_system(
            {
                "name": "Blunder Product",
                "background": "#090B0D",
                "surface": "#15191E",
                "text": "#F2F5F7",
                "muted": "#AAB4BE",
                "primary": "#C9F269",
                "accent": "#74B9FF",
                "danger": "#FF6B7A",
                "focus": "#F4D35E",
                "typeBasePx": 16,
                "typeRatio": 1.25,
                "spacingBasePx": 4,
                "radiusBasePx": 4,
            }
        )
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(all(report["gates"].values()))
        self.assertIn("--blunder-color-primary", report["css"])
        self.assertEqual(report["dtcg"]["color"]["$type"], "color")
        self.assertEqual(report["dtcg"]["space"]["md"]["$value"], {"value": 12, "unit": "px"})

    def test_p28_asset_intelligence_enforces_provenance_license_and_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "assets" / "hero.png").write_bytes(rgba_png(8, 8, (20, 30, 40, 255)))
            provenance = root / "assets.provenance.json"
            provenance.write_text(json.dumps({"schema": "obtuse.blunder.asset-provenance.v1", "assets": [{"path": "assets/hero.png", "source": "first-party", "license": "first-party"}]}), encoding="utf-8")
            report = asset_intelligence_report(
                root,
                {"maximumAssetBytes": 100_000, "maximumTotalBytes": 200_000, "maximumImagePixels": 1_000},
                provenance,
                ("first-party",),
            )
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["assets"][0]["dimensions"], [8, 8])

    def test_p29_polyglot_intelligence_indexes_product_languages_and_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.ts").write_text("export function launchAtlas(): void { const value = 1; }\n", encoding="utf-8")
            (root / "App.swift").write_text("struct AtlasView { func launchAtlas() { } }\n", encoding="utf-8")
            (root / "index.html").write_text('<main id="launchAtlas"><a href="/start">Start</a></main>\n', encoding="utf-8")
            report = polyglot_index(root)
            references = polyglot_references(root, "launchAtlas", 20)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["languageCounts"], {"markup": 1, "swift": 1, "typescript": 1})
            self.assertEqual(references["count"], 3)

    def test_p30_product_arena_requires_complete_product_evidence(self) -> None:
        cases = load_product_suite(REPO_ROOT / "configs" / "obtuse" / "blunder-product-evaluation-suite.json")
        measurement = parse_measurement(
            {
                "caseId": "mobile-onboarding-001",
                "candidateId": "blunder-atlas",
                "reports": {
                    "blueprint": passing_report("READY_FOR_PRODUCT_PLANNING"),
                    "visual": passing_report("PASS"),
                    "device": passing_report("PASS"),
                    "platform": passing_report("READY_FOR_EXECUTABLE_PROOF"),
                    "design": passing_report("PASS"),
                    "assets": passing_report("PASS"),
                    "polyglot": passing_report("PASS"),
                },
                "metrics": {"journeyCompletionRate": 1.0, "accessibilityCritical": 0, "performanceMetric": 1000, "operatorInterventions": 0},
                "operatorStatus": "accepted",
                "elapsedSeconds": 300,
                "costUsd": 0.25,
            }
        )
        report = product_arena_report(cases, (measurement,), ())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["averageScore"], 100.0)
        self.assertEqual(report["falseAcceptRate"], 0.0)


if __name__ == "__main__":
    unittest.main()
