"""
Contrast checks for the high-contrast themes in css/themes.css.

The default palettes are deliberately left as the user chose them; the two
High Contrast themes are the accessibility escape hatch. This test keeps them
from regressing: body text must stay at least 7:1 and every status/on-colour
pair at least 4.5:1.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

THEMES_CSS = (
    Path(__file__).parent.parent
    / "scripts"
    / "web"
    / "static"
    / "css"
    / "themes.css"
)

# (theme id, minimum ratio for --text on --bg)
HIGH_CONTRAST_THEMES: tuple[str, ...] = ("high-contrast", "high-contrast-light")

# (foreground token, background token, minimum contrast ratio)
PAIRS: tuple[tuple[str, str, float], ...] = (
    ("--text", "--bg", 7.0),
    ("--text-dim", "--bg", 4.5),
    ("--text-dim", "--bg-raised", 4.5),
    ("--text-dim", "--bg-input", 4.5),
    ("--accent", "--bg", 4.5),
    ("--on-accent", "--accent", 4.5),
    ("--success", "--bg", 4.5),
    ("--warning", "--bg", 4.5),
    ("--warning", "--bg-input", 4.5),
    ("--error", "--bg", 4.5),
    ("--on-success", "--success", 4.5),
    ("--on-warning", "--warning", 4.5),
    ("--on-error", "--error", 4.5),
)

_BLOCK = re.compile(
    r':root\[data-theme="(?P<id>[a-z-]+)"\]\s*\{(?P<body>[^}]*)\}'
)
_PROP = re.compile(r"(?P<name>--[a-z-]+):\s*(?P<value>#[0-9a-fA-F]{6})\s*;")


def srgb(channel: int) -> float:
    """Linearise one 8-bit sRGB channel."""
    value: float = channel / 255
    if value <= 0.03928:
        return value / 12.92
    return float(((value + 0.055) / 1.055) ** 2.4)


def relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance of a #rrggbb colour."""
    digits: str = hex_colour.lstrip("#")
    red, green, blue = (int(digits[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * srgb(red) + 0.7152 * srgb(green) + 0.0722 * srgb(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two #rrggbb colours."""
    first: float = relative_luminance(foreground)
    second: float = relative_luminance(background)
    lighter: float = max(first, second)
    darker: float = min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def load_palettes() -> dict[str, dict[str, str]]:
    """Read the hex-valued custom properties of every data-theme block."""
    palettes: dict[str, dict[str, str]] = {}
    for match in _BLOCK.finditer(THEMES_CSS.read_text(encoding="utf-8")):
        palette: dict[str, str] = {}
        for prop in _PROP.finditer(match.group("body")):
            palette[prop.group("name")] = prop.group("value")
        palettes[match.group("id")] = palette
    return palettes


def test_high_contrast_themes_exist() -> None:
    """Both High Contrast themes are defined in the stylesheet."""
    palettes: dict[str, dict[str, str]] = load_palettes()
    for theme in HIGH_CONTRAST_THEMES:
        assert theme in palettes, f"{theme} is missing from themes.css"
        assert "--text" in palettes[theme]


def test_high_contrast_themes_meet_aa() -> None:
    """Every high-contrast token pair clears its contrast target."""
    palettes: dict[str, dict[str, str]] = load_palettes()
    failures: list[str] = []
    for theme in HIGH_CONTRAST_THEMES:
        palette: dict[str, str] = palettes[theme]
        for foreground_token, background_token, minimum in PAIRS:
            ratio: float = contrast_ratio(
                palette[foreground_token], palette[background_token]
            )
            if ratio < minimum:
                failures.append(
                    f"{theme}: {foreground_token} on {background_token} "
                    f"is {ratio:.2f}:1, expected at least {minimum:.1f}:1"
                )
    assert not failures, "\n".join(failures)
