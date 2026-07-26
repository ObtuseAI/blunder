from __future__ import annotations

import hashlib
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


VISUAL_SCHEMA = "obtuse.blunder.visual-judgment.v1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


@dataclass(frozen=True)
class Raster:
    width: int
    height: int
    rgba: bytes
    sha256: str


def safe_image_path(root: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("Visual evidence path is empty.")
    candidate = (root.resolve() / normalized).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"Visual evidence escapes the project root: {relative}")
    if not candidate.is_file() or candidate.suffix.lower() != ".png":
        raise ValueError(f"Visual evidence must be an existing PNG: {relative}")
    if candidate.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"Visual evidence exceeds 32 MiB: {relative}")
    return candidate


def paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    diagonal_distance = abs(estimate - upper_left)
    return left if left_distance <= up_distance and left_distance <= diagonal_distance else up if up_distance <= diagonal_distance else upper_left


def reconstruct_scanline(filtered: bytes, previous: bytes, bytes_per_pixel: int, filter_type: int) -> bytes:
    output = bytearray(len(filtered))
    for index, value in enumerate(filtered):
        left = output[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        up = previous[index] if previous else 0
        upper_left = previous[index - bytes_per_pixel] if previous and index >= bytes_per_pixel else 0
        predictor = (
            0
            if filter_type == 0
            else left
            if filter_type == 1
            else up
            if filter_type == 2
            else (left + up) // 2
            if filter_type == 3
            else paeth(left, up, upper_left)
            if filter_type == 4
            else -1
        )
        if predictor < 0:
            raise ValueError(f"PNG uses unsupported filter type {filter_type}.")
        output[index] = (value + predictor) & 0xFF
    return bytes(output)


def rgba_scanline(scanline: bytes, color_type: int) -> bytes:
    if color_type == 6:
        return scanline
    if color_type == 2:
        return b"".join(scanline[index : index + 3] + b"\xFF" for index in range(0, len(scanline), 3))
    if color_type == 0:
        return b"".join(bytes((value, value, value, 255)) for value in scanline)
    raise ValueError(f"PNG color type {color_type} is unsupported.")


def read_png(path: Path) -> Raster:
    content = path.read_bytes()
    if not content.startswith(PNG_SIGNATURE):
        raise ValueError(f"PNG signature is invalid: {path}")
    offset = len(PNG_SIGNATURE)
    width = 0
    height = 0
    bit_depth = 0
    color_type = -1
    compressed = bytearray()
    while offset < len(content):
        if offset + 12 > len(content):
            raise ValueError(f"PNG chunk is truncated: {path}")
        length = struct.unpack(">I", content[offset : offset + 4])[0]
        kind = content[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > len(content):
            raise ValueError(f"PNG chunk data is truncated: {path}")
        data = content[data_start:data_end]
        expected_crc = struct.unpack(">I", content[data_end : data_end + 4])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != expected_crc:
            raise ValueError(f"PNG chunk CRC is invalid: {path}")
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if not width or not height or bit_depth != 8 or color_type not in {0, 2, 6}:
                raise ValueError("PNG must be 8-bit non-paletted grayscale, RGB, or RGBA.")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("PNG exceeds the 20-megapixel visual evidence ceiling.")
            if compression != 0 or filtering != 0 or interlace != 0:
                raise ValueError("PNG must use standard compression, filtering, and no interlace.")
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            break
        offset = data_end + 4
    if not width or not compressed:
        raise ValueError(f"PNG is missing IHDR or IDAT data: {path}")
    bytes_per_pixel = {0: 1, 2: 3, 6: 4}[color_type]
    stride = width * bytes_per_pixel
    inflated = zlib.decompress(bytes(compressed))
    if len(inflated) != height * (stride + 1):
        raise ValueError(f"PNG decompressed size does not match its dimensions: {path}")
    previous = b""
    pixels = bytearray()
    for row in range(height):
        start = row * (stride + 1)
        scanline = reconstruct_scanline(
            inflated[start + 1 : start + 1 + stride],
            previous,
            bytes_per_pixel,
            inflated[start],
        )
        pixels.extend(rgba_scanline(scanline, color_type))
        previous = scanline
    return Raster(width, height, bytes(pixels), hashlib.sha256(content).hexdigest().upper())


def visual_difference(baseline: Raster, candidate: Raster, pixel_threshold: int) -> dict[str, object]:
    if baseline.width != candidate.width or baseline.height != candidate.height:
        return {
            "comparable": False,
            "baselineSize": [baseline.width, baseline.height],
            "candidateSize": [candidate.width, candidate.height],
            "changedPercent": 100.0,
            "meanChannelDelta": 255.0,
            "boundingBox": [0, 0, max(baseline.width, candidate.width), max(baseline.height, candidate.height)],
        }
    changed = 0
    total_delta = 0
    minimum_x = baseline.width
    minimum_y = baseline.height
    maximum_x = -1
    maximum_y = -1
    for pixel in range(baseline.width * baseline.height):
        offset = pixel * 4
        deltas = tuple(abs(baseline.rgba[offset + channel] - candidate.rgba[offset + channel]) for channel in range(4))
        total_delta += sum(deltas)
        if max(deltas) <= pixel_threshold:
            continue
        changed += 1
        x = pixel % baseline.width
        y = pixel // baseline.width
        minimum_x = min(minimum_x, x)
        minimum_y = min(minimum_y, y)
        maximum_x = max(maximum_x, x)
        maximum_y = max(maximum_y, y)
    pixels = baseline.width * baseline.height
    return {
        "comparable": True,
        "baselineSize": [baseline.width, baseline.height],
        "candidateSize": [candidate.width, candidate.height],
        "changedPixels": changed,
        "changedPercent": round(changed / pixels * 100, 6),
        "meanChannelDelta": round(total_delta / (pixels * 4), 6),
        "boundingBox": None if not changed else [minimum_x, minimum_y, maximum_x + 1, maximum_y + 1],
    }


def visual_judgment_report(root: Path, cases: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not cases:
        raise ValueError("Visual judgment requires at least one evidence case.")
    results: list[dict[str, object]] = []
    for raw in cases:
        case_id = str(raw.get("id") or "").strip()
        if not case_id:
            raise ValueError("Every visual case requires an id.")
        pixel_threshold = int(raw.get("pixelThreshold") or 0)
        maximum_changed = float(raw.get("maximumChangedPercent") or 0)
        maximum_mean_delta = float(raw.get("maximumMeanChannelDelta") or 0)
        if not 0 <= pixel_threshold <= 255 or not 0 <= maximum_changed <= 100 or not 0 <= maximum_mean_delta <= 255:
            raise ValueError(f"Visual case {case_id} has invalid thresholds.")
        baseline_path = safe_image_path(root, str(raw.get("baseline") or ""))
        candidate_path = safe_image_path(root, str(raw.get("candidate") or ""))
        baseline = read_png(baseline_path)
        candidate = read_png(candidate_path)
        difference = visual_difference(baseline, candidate, pixel_threshold)
        raw_layout = raw.get("layout")
        layout = raw_layout if isinstance(raw_layout, dict) else {}
        gates = {
            "dimensionsMatch": difference["comparable"] is True,
            "pixelBudgetPassed": float(difference["changedPercent"]) <= maximum_changed,
            "meanDeltaPassed": float(difference["meanChannelDelta"]) <= maximum_mean_delta,
            "noOverflow": int(layout.get("overflowCount") or 0) == 0,
            "noClipping": int(layout.get("clippedCount") or 0) == 0,
            "focusVisible": layout.get("focusVisible") is not False,
            "noConsoleErrors": int(layout.get("consoleErrors") or 0) == 0,
        }
        results.append(
            {
                "id": case_id,
                "status": "PASS" if all(gates.values()) else "FAIL",
                "baseline": {"path": baseline_path.relative_to(root.resolve()).as_posix(), "sha256": baseline.sha256},
                "candidate": {"path": candidate_path.relative_to(root.resolve()).as_posix(), "sha256": candidate.sha256},
                "difference": difference,
                "layout": dict(layout),
                "gates": gates,
            }
        )
    return {
        "schema": VISUAL_SCHEMA,
        "status": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL",
        "cases": results,
        "caseCount": len(results),
        "failedCases": sum(1 for item in results if item["status"] != "PASS"),
        "providerCalls": 0,
        "sourceMutation": False,
    }


def write_visual_report(path: Path, report: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(report), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
