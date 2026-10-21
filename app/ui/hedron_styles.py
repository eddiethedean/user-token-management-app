"""Hedron defaults with shell tokens and scoped transfer presentation fallbacks."""

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
    # Hedron 1.1.3 emits GridItem's align marker without a corresponding
    # alignment rule. Keep this fallback confined to the transfer status cell.
    # Upstream: eddiethedean/hedron#979 (grid) and #980 (text alignment).
    # Directional connector motion is also unavailable (#981). Its preserved
    # decorative DOM stays stable while the server-authored parent state stops
    # motion and selects the terminal glyph, including across HTMX refreshes.
    transfer_presentation = """@layer components {
  .data-mover-transfer-status {
    align-self: center;
    text-align: center;
  }
  [data-transfer-route] {
    --transfer-ink: var(--hedron-color-accent, var(--hedron-default-accent));
  }
  [data-transfer-state="succeeded"] {
    --transfer-ink: var(--hedron-default-success);
  }
  [data-transfer-state="cancelled"], [data-transfer-state="blocked"] {
    --transfer-ink: var(--hedron-default-warning);
  }
  [data-transfer-state="failed"], [data-transfer-state="failed_needs_reconciliation"] {
    --transfer-ink: var(--hedron-color-danger, var(--hedron-default-danger));
  }
  .data-mover-transfer-signal {
    position: relative;
    isolation: isolate;
    inline-size: 100%;
    block-size: 3.5rem;
    color: var(--transfer-ink);
  }
  .data-mover-transfer-stream {
    position: absolute;
    inset-inline: 0;
    inset-block-start: calc(50% - .375rem);
    block-size: .75rem;
    overflow: hidden;
  }
  .data-mover-transfer-stream::before {
    content: "";
    position: absolute;
    inset-inline: 0;
    inset-block-start: calc(50% - 1px);
    block-size: 2px;
    background: linear-gradient(90deg, transparent, color-mix(in srgb, currentColor 45%, transparent) 20% 80%, transparent);
  }
  .data-mover-transfer-hub {
    position: relative;
    display: grid;
    place-items: center;
    inline-size: 3rem;
    block-size: 3rem;
    margin: .25rem auto;
    border: 1px solid color-mix(in srgb, currentColor 35%, transparent);
    border-radius: 50%;
    background: var(--hedron-color-surface, var(--hedron-default-surface));
  }
  .data-mover-transfer-hub::after {
    content: "";
    position: absolute;
    inset: -.35rem;
    border: 1px solid currentColor;
    border-radius: inherit;
    opacity: 0;
  }
  .data-mover-transfer-check, .data-mover-transfer-pause,
  .data-mover-transfer-attention, .data-mover-transfer-packet {
    display: none;
  }
  [data-transfer-state="succeeded"] .data-mover-transfer-check,
  [data-transfer-state="cancelled"] .data-mover-transfer-pause,
  :is([data-transfer-state="failed"], [data-transfer-state="blocked"],
      [data-transfer-state="failed_needs_reconciliation"]) .data-mover-transfer-attention {
    display: inline-flex;
  }
  :is([data-transfer-state="succeeded"], [data-transfer-state="cancelled"],
      [data-transfer-state="failed"], [data-transfer-state="blocked"],
      [data-transfer-state="failed_needs_reconciliation"]) .data-mover-transfer-arrow {
    display: none;
  }
  [data-transfer-state="loading"] .data-mover-transfer-packet {
    display: block;
    position: absolute;
    inset: 0;
    animation: data-mover-transfer-flow 2.4s linear infinite;
  }
  .data-mover-transfer-packet::before {
    content: "";
    position: absolute;
    inset-block-start: calc(50% - 2px);
    inline-size: .65rem;
    block-size: 4px;
    border-radius: 1rem;
    background: currentColor;
    box-shadow: 0 0 .4rem color-mix(in srgb, currentColor 45%, transparent);
  }
  [data-transfer-state="loading"] .data-mover-transfer-packet:nth-child(2) {
    animation-delay: -.8s;
  }
  [data-transfer-state="loading"] .data-mover-transfer-packet:nth-child(3) {
    animation-delay: -1.6s;
  }
  [data-transfer-state="loading"] .data-mover-transfer-hub::after {
    animation: data-mover-transfer-glow 2.4s ease-out infinite;
  }
  @keyframes data-mover-transfer-flow {
    0% { transform: translateX(-.65rem); opacity: 0; }
    12%, 82% { opacity: 1; }
    100% { transform: translateX(100%); opacity: 0; }
  }
  @keyframes data-mover-transfer-glow {
    0% { transform: scale(.88); opacity: .3; }
    100% { transform: scale(1.15); opacity: 0; }
  }
  @media (prefers-reduced-motion: reduce), print {
    [data-transfer-route] .data-mover-transfer-packet {
      display: none;
      animation: none;
    }
    [data-transfer-route] .data-mover-transfer-hub::after {
      animation: none;
    }
  }
  @media (forced-colors: active) {
    .data-mover-transfer-hub { background: Canvas; border-color: CanvasText; }
    .data-mover-transfer-stream::before { background: CanvasText; }
    [data-transfer-route] .data-mover-transfer-packet { display: none; }
    [data-transfer-route] .data-mover-transfer-hub::after { animation: none; }
  }
}
"""
    return f"{builtin_styles}\n{shell_geometry_tokens}\n{transfer_presentation}"


__all__ = ["desktop_default_styles"]
