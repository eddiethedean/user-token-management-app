"""Hedron's native default stylesheet for desktop and mobile layouts."""

from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from importlib import resources

from hedron_core.theme import design_system_vars, folio_theme

_FOLIO_NAV_WIDTH = "12rem"

_MEDIA_RULE = re.compile(r"@media\s*(?P<condition>[^{}]+)\{", re.IGNORECASE)
_VIEWPORT_CONDITION = re.compile(
    r"max-width\s*:|hover\s*:\s*none",
    re.IGNORECASE,
)


def _matching_brace(stylesheet: str, opening: int) -> int:
    depth = 0
    quote: str | None = None
    comment = False
    escaped = False
    for index in range(opening, len(stylesheet)):
        character = stylesheet[index]
        following = stylesheet[index + 1] if index + 1 < len(stylesheet) else ""
        if comment:
            if character == "*" and following == "/":
                comment = False
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character == "/" and following == "*":
            comment = True
        elif character in {'"', "'"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("Unbalanced CSS block in Hedron's default stylesheet")


def _without_viewport_media(stylesheet: str) -> str:
    """Remove mobile viewport and touch media blocks while preserving other CSS."""

    output: list[str] = []
    cursor = 0
    while match := _MEDIA_RULE.search(stylesheet, cursor):
        start = match.start()
        opening = match.end() - 1
        closing = _matching_brace(stylesheet, opening)
        output.append(stylesheet[cursor:start])
        if not _VIEWPORT_CONDITION.search(match.group("condition")):
            output.append(stylesheet[start : closing + 1])
        cursor = closing + 1
    output.append(stylesheet[cursor:])
    return "".join(output)


@lru_cache(maxsize=1)
def desktop_default_styles() -> str:
    """Return Hedron's defaults with the native Folio shell geometry applied.

    Hedron's build command emits the selected theme's registered metadata.  The
    app intentionally keeps Folio as its registered theme, so emit the one
    native Folio token override needed by this product here rather than adding
    an app-authored selector or a second theme.  This keeps the navigation
    column compact while retaining Hedron's normal min-width guardrail.
    """

    builtin_styles = (
        resources.files("hedron_core")
        .joinpath("static/hedron-default.css")
        .read_text(encoding="utf-8")
    )
    folio_shell_tokens = design_system_vars(replace(folio_theme(), nav_width=_FOLIO_NAV_WIDTH))
    native_nav_token = folio_shell_tokens["--hedron-nav-width"]
    shell_geometry_tokens = (
        f"@layer tokens {{\n:root {{\n  --hedron-nav-width: {native_nav_token};\n}}\n}}\n"
    )
    return f"{builtin_styles}\n{shell_geometry_tokens}"


__all__ = ["desktop_default_styles"]
