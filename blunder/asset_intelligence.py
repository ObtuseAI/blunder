from __future__ import annotations

import hashlib
import json
import re
import struct
import wave
from pathlib import Path
from typing import Mapping, Sequence


ASSET_SCHEMA = "obtuse.blunder.asset-intelligence.v1"
ASSET_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".wav", ".ogg", ".mp3", ".ttf", ".otf", ".woff", ".woff2", ".gltf", ".glb", ".fbx", ".obj"}
)
IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
BLOCKED_PARTS: frozenset[str] = frozenset({".git", "node_modules", "artifacts", "dist", "build", ".godot", "Library"})


def png_dimensions(content: bytes) -> tuple[int, int] | None:
    return struct.unpack(">II", content[16:24]) if len(content) >= 24 and content.startswith(b"\x89PNG\r\n\x1a\n") else None


def gif_dimensions(content: bytes) -> tuple[int, int] | None:
    return struct.unpack("<HH", content[6:10]) if len(content) >= 10 and content[:6] in {b"GIF87a", b"GIF89a"} else None


def jpeg_dimensions(content: bytes) -> tuple[int, int] | None:
    if len(content) < 4 or content[:2] != b"\xFF\xD8":
        return None
    offset = 2
    while offset + 9 < len(content):
        if content[offset] != 0xFF:
            offset += 1
            continue
        marker = content[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(content):
            return None
        length = struct.unpack(">H", content[offset : offset + 2])[0]
        if length < 2 or offset + length > len(content):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", content[offset + 3 : offset + 7])
            return width, height
        offset += length
    return None


def svg_facts(content: str) -> tuple[tuple[int, int] | None, list[str]]:
    view_box = re.search(r"\bviewBox\s*=\s*[\"']\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)", content, re.IGNORECASE)
    dimensions = (round(float(view_box.group(1))), round(float(view_box.group(2)))) if view_box else None
    findings: list[str] = []
    if re.search(r"<script\b|\bon\w+\s*=", content, re.IGNORECASE):
        findings.append("SVG_ACTIVE_SCRIPTING")
    if re.search(r"(?:href|src)\s*=\s*[\"'](?:https?:)?//", content, re.IGNORECASE):
        findings.append("SVG_EXTERNAL_RESOURCE")
    if dimensions is None:
        findings.append("SVG_VIEWBOX_MISSING")
    return dimensions, findings


def image_facts(path: Path, content: bytes) -> tuple[tuple[int, int] | None, list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return png_dimensions(content), []
    if suffix == ".gif":
        return gif_dimensions(content), []
    if suffix in {".jpg", ".jpeg"}:
        return jpeg_dimensions(content), []
    if suffix == ".svg":
        return svg_facts(content.decode("utf-8", errors="replace"))
    return None, ["DIMENSIONS_UNSUPPORTED"] if suffix == ".webp" else []


def wave_facts(path: Path) -> dict[str, object]:
    try:
        with wave.open(str(path), "rb") as audio:
            frames = audio.getnframes()
            rate = audio.getframerate()
            return {
                "channels": audio.getnchannels(),
                "sampleRate": rate,
                "durationSeconds": round(frames / rate, 4) if rate else None,
                "sampleWidthBytes": audio.getsampwidth(),
            }
    except (wave.Error, EOFError) as error:
        return {"parseError": str(error)}


def asset_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in {".wav", ".ogg", ".mp3"}:
        return "audio"
    if suffix in {".ttf", ".otf", ".woff", ".woff2"}:
        return "font"
    return "model"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def provenance_records(path: Path | None) -> dict[str, Mapping[str, object]]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read asset provenance {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != "obtuse.blunder.asset-provenance.v1":
        raise ValueError("Asset provenance uses an unsupported contract.")
    raw = value.get("assets")
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValueError("Asset provenance assets must be an object list.")
    return {str(item.get("path") or "").replace("\\", "/"): item for item in raw}


def asset_intelligence_report(
    root: Path,
    budgets: Mapping[str, object],
    provenance_path: Path | None,
    allowed_licenses: Sequence[str],
) -> dict[str, object]:
    resolved = root.resolve()
    maximum_asset_bytes = int(budgets.get("maximumAssetBytes") or 0)
    maximum_total_bytes = int(budgets.get("maximumTotalBytes") or 0)
    maximum_image_pixels = int(budgets.get("maximumImagePixels") or 0)
    if min(maximum_asset_bytes, maximum_total_bytes, maximum_image_pixels) <= 0:
        raise ValueError("Asset budgets must all be positive.")
    allowed = frozenset(str(item).strip().lower() for item in allowed_licenses)
    if not allowed:
        raise ValueError("Asset intelligence requires at least one explicitly allowed license.")
    if provenance_path is not None and not provenance_path.resolve().is_relative_to(resolved):
        raise ValueError("Asset provenance must stay inside the project root.")
    provenance = provenance_records(provenance_path)
    paths = tuple(
        path
        for path in resolved.rglob("*")
        if path.is_file()
        and path.suffix.lower() in ASSET_SUFFIXES
        and not set(path.relative_to(resolved).parts).intersection(BLOCKED_PARTS)
    )
    records: list[dict[str, object]] = []
    digests: dict[str, list[str]] = {}
    total_bytes = 0
    for path in paths:
        relative = path.relative_to(resolved).as_posix()
        size = path.stat().st_size
        oversized = size > maximum_asset_bytes
        content = path.read_bytes() if not oversized else b""
        digest = file_sha256(path)
        digests.setdefault(digest, []).append(relative)
        total_bytes += size
        dimensions, findings = image_facts(path, content) if path.suffix.lower() in IMAGE_SUFFIXES and not oversized else (None, ["ASSET_TOO_LARGE_TO_PARSE"] if oversized else [])
        pixels = dimensions[0] * dimensions[1] if dimensions else None
        provenance_row = provenance.get(relative, {})
        license_name = str(provenance_row.get("license") or "unknown").lower()
        gates = {
            "individualBudget": not oversized,
            "pixelBudget": pixels is None or pixels <= maximum_image_pixels,
            "provenancePresent": bool(provenance_row.get("source")),
            "licenseAllowed": license_name in allowed,
            "activeContentAbsent": not findings or not set(findings).intersection({"SVG_ACTIVE_SCRIPTING", "SVG_EXTERNAL_RESOURCE"}),
        }
        records.append(
            {
                "path": relative,
                "kind": asset_kind(path),
                "bytes": size,
                "sha256": digest,
                "dimensions": list(dimensions) if dimensions else None,
                "pixels": pixels,
                "audio": wave_facts(path) if path.suffix.lower() == ".wav" and not oversized else None,
                "provenance": dict(provenance_row),
                "findings": findings,
                "gates": gates,
                "status": "PASS" if all(gates.values()) else "FAIL",
            }
        )
    duplicate_groups = [items for items in digests.values() if len(items) > 1]
    global_gates = {
        "assetsPresent": bool(records),
        "totalBudget": total_bytes <= maximum_total_bytes,
        "everyAssetPassed": bool(records) and all(item["status"] == "PASS" for item in records),
        "noDuplicatePayloads": not duplicate_groups,
    }
    return {
        "schema": ASSET_SCHEMA,
        "status": "PASS" if all(global_gates.values()) else "FAIL",
        "assets": records,
        "assetCount": len(records),
        "totalBytes": total_bytes,
        "duplicates": duplicate_groups,
        "budgets": dict(budgets),
        "allowedLicenses": sorted(allowed),
        "gates": global_gates,
        "sourceMutation": False,
        "providerCalls": 0,
    }
