from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


DESIGN_SYSTEM_SCHEMA = "obtuse.blunder.design-system.v1"
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def color(value: Mapping[str, object], key: str) -> str:
    observed = str(value.get(key) or "")
    if HEX_COLOR.fullmatch(observed) is None:
        raise ValueError(f"Design-system color {key} must use six-digit hexadecimal notation.")
    return observed.upper()


def channel_luminance(channel: int) -> float:
    normalized = channel / 255
    return normalized / 12.92 if normalized <= 0.04045 else math.pow((normalized + 0.055) / 1.055, 2.4)


def luminance(hex_color: str) -> float:
    channels = tuple(int(hex_color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel_luminance(channels[0]) + 0.7152 * channel_luminance(channels[1]) + 0.0722 * channel_luminance(channels[2])


def contrast_ratio(first: str, second: str) -> float:
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return round((high + 0.05) / (low + 0.05), 3)


def modular_scale(base: float, ratio: float) -> dict[str, float]:
    if not 12 <= base <= 22 or not 1.05 <= ratio <= 1.5:
        raise ValueError("Typography requires a 12-22px base and a 1.05-1.5 scale ratio.")
    return {
        name: round(base * math.pow(ratio, exponent), 3)
        for name, exponent in (
            ("caption", -1),
            ("body", 0),
            ("label", 0),
            ("title", 1),
            ("headline", 2),
            ("display", 3),
        )
    }


def spacing_scale(base: int) -> dict[str, int]:
    if base < 2 or base > 12:
        raise ValueError("Spacing base must be from 2 through 12 pixels.")
    return {name: base * multiplier for name, multiplier in (("xs", 1), ("sm", 2), ("md", 3), ("lg", 5), ("xl", 8), ("2xl", 13))}


def css_variables(tokens: Mapping[str, object]) -> str:
    rows: list[str] = [":root {"]
    for group, raw_values in tokens.items():
        if not isinstance(raw_values, dict):
            continue
        for name, value in raw_values.items():
            suffix = "px" if group in {"space", "radius", "type"} and isinstance(value, (int, float)) else ""
            rows.append(f"  --blunder-{group}-{name}: {value}{suffix};")
    rows.append("}")
    return "\n".join(rows) + "\n"


def dtcg_value(group: str, value: object) -> object:
    if group in {"space", "radius", "type"} and isinstance(value, (int, float)):
        return {"value": value, "unit": "px"}
    if group == "breakpoint" and isinstance(value, str) and value.endswith("px"):
        return {"value": float(value[:-2]), "unit": "px"}
    if group == "motion" and isinstance(value, str) and value.endswith("ms"):
        return {"value": float(value[:-2]), "unit": "ms"}
    return value


def dtcg_tokens(tokens: Mapping[str, object]) -> dict[str, object]:
    types = {"color": "color", "type": "dimension", "space": "dimension", "radius": "dimension", "motion": "duration", "breakpoint": "dimension"}
    result: dict[str, object] = {}
    for group, raw_values in tokens.items():
        if not isinstance(raw_values, dict) or group not in types:
            continue
        result[group] = {"$type": types[group]} | {
            str(name): {"$value": dtcg_value(group, value)}
            for name, value in raw_values.items()
        }
    return result


def compile_design_system(value: Mapping[str, object]) -> dict[str, object]:
    name = str(value.get("name") or "").strip()
    if len(name) < 2 or len(name) > 80:
        raise ValueError("Design-system name must contain from 2 through 80 characters.")
    colors = {
        "background": color(value, "background"),
        "surface": color(value, "surface"),
        "text": color(value, "text"),
        "muted": color(value, "muted"),
        "primary": color(value, "primary"),
        "accent": color(value, "accent"),
        "danger": color(value, "danger"),
        "focus": color(value, "focus"),
    }
    type_values = modular_scale(float(value.get("typeBasePx") or 0), float(value.get("typeRatio") or 0))
    space_values = spacing_scale(int(value.get("spacingBasePx") or 0))
    radius_base = int(value.get("radiusBasePx") or 0)
    if radius_base < 0 or radius_base > 24:
        raise ValueError("Radius base must be from 0 through 24 pixels.")
    ratios = {
        "textOnBackground": contrast_ratio(colors["text"], colors["background"]),
        "textOnSurface": contrast_ratio(colors["text"], colors["surface"]),
        "mutedOnBackground": contrast_ratio(colors["muted"], colors["background"]),
        "primaryOnBackground": contrast_ratio(colors["primary"], colors["background"]),
        "accentOnBackground": contrast_ratio(colors["accent"], colors["background"]),
        "dangerOnBackground": contrast_ratio(colors["danger"], colors["background"]),
        "focusOnBackground": contrast_ratio(colors["focus"], colors["background"]),
    }
    gates = {
        "bodyTextAA": ratios["textOnBackground"] >= 4.5 and ratios["textOnSurface"] >= 4.5,
        "mutedTextAA": ratios["mutedOnBackground"] >= 4.5,
        "primaryNonTextContrast": ratios["primaryOnBackground"] >= 3.0,
        "accentNonTextContrast": ratios["accentOnBackground"] >= 3.0,
        "dangerNonTextContrast": ratios["dangerOnBackground"] >= 3.0,
        "focusVisibleContrast": ratios["focusOnBackground"] >= 3.0,
    }
    tokens: dict[str, object] = {
        "color": colors,
        "type": type_values,
        "space": space_values,
        "radius": {"sm": radius_base, "md": radius_base * 2, "lg": radius_base * 3, "pill": 999},
        "motion": {"instant": "0ms", "fast": "120ms", "normal": "220ms", "slow": "360ms", "reduced": "0ms"},
        "breakpoint": {"compact": "480px", "medium": "768px", "wide": "1200px", "ultra": "1600px"},
    }
    return {
        "schema": DESIGN_SYSTEM_SCHEMA,
        "createdAt": utc_now(),
        "name": name,
        "status": "PASS" if all(gates.values()) else "FAIL_CONTRAST_GATES",
        "tokens": tokens,
        "contrast": ratios,
        "gates": gates,
        "componentStates": ["rest", "hover", "focus-visible", "active", "disabled", "loading", "error", "success"],
        "responsiveRules": [
            "content reflows without horizontal overflow at every breakpoint",
            "touch targets remain at least 44 by 44 CSS pixels",
            "text remains usable at 200 percent zoom",
            "reduced-motion removes nonessential animation",
        ],
        "css": css_variables(tokens),
        "dtcg": dtcg_tokens(tokens),
        "sourceMutation": False,
        "providerCalls": 0,
    }


def write_design_system(path: Path, report: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(report), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
