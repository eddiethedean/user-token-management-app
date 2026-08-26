"""Desktop-only derivative of Hedron's native default stylesheet."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

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
    """Return Hedron's default styles without mobile viewport media rules."""

    stylesheet = (
        resources.files("hedron_core")
        .joinpath("static/hedron-default.css")
        .read_text(encoding="utf-8")
    )
    return _without_viewport_media(stylesheet)


__all__ = ["desktop_default_styles"]
