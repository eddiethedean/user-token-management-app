"""Data Mover's explicit Hedron 0.60 presentation contract."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, TypeVar

from hedron import (
    Card,
    Color,
    DesignSystem,
    RecipeFamily,
    StyleRecipe,
    ThemeBuilder,
    register_recipe_family,
    validate_theme_spec,
)
from hedron_core import Component
from hedron_core.theme import Theme, aurora_theme

_ComponentT = TypeVar("_ComponentT", bound=Component)


# Build a first-party Data Mover brand from Hedron's 0.60 typed design system.
# Aurora remains the accessibility-tested base; the brand compiler owns the
# palette, geometry, typography, motion, and navigation groups.
_BRAND_DESIGN = DesignSystem.brand(
    "data-mover",
    accent=Color.oklch(0.68, 0.18, 275),
    base=aurora_theme(),
    density="comfortable",
    geometry="soft",
    typography="system-sans",
    elevation="subtle",
    motion="standard",
    navigation="wide",
)

# 0.60's extensible recipe-family contract lets the flow canvas declare its
# presentation vocabulary without private CSS or behavior-shaped props.
DATA_MOVER_FLOW_FAMILY = RecipeFamily(
    name="flow",
    fields={
        "direction": ("horizontal", "vertical"),
        "background": ("none", "grid", "dots"),
        "overflow": ("visible", "auto", "scroll"),
        "min_size": ("none", "sm", "md", "lg"),
    },
    components=("ConnectorFlow", "ProcessFlow"),
)
register_recipe_family(DATA_MOVER_FLOW_FAMILY)

# ThemeBuilder is the canonical 0.60 authoring layer. The legacy Theme bridge
# remains deliberate because Hedron's application shell consumes its resolved
# CSS contract while keeping the legacy Theme bridge compatible.
DATA_MOVER_THEME_SPEC = (
    ThemeBuilder.from_theme(_BRAND_DESIGN.to_theme())
    .alias("color.canvas", "color.bg")
    .groups(workspace="workspace", auth="auth")
    .recipe(
        "data-mover-flow",
        {
            "direction": "horizontal",
            "background": "grid",
            "overflow": "auto",
            "min_size": "md",
        },
    )
    .accessibility_mode(
        "forced-colors",
        **{
            "color.bg": "Canvas",
            "color.fg": "CanvasText",
            "color.accent": "Highlight",
            "color.focus": "Highlight",
            "color.danger": "Mark",
        },
    )
    .accessibility_mode(
        "more-contrast",
        **{"color.accent": "#a8aaff", "color.focus": "#ffffff"},
    )
    .metadata(product="data-mover", release="0.60")
    .profile("workflow")
    .build()
)
_THEME_REPORT = validate_theme_spec(DATA_MOVER_THEME_SPEC, profile="workflow")
if not _THEME_REPORT.ok:
    raise ValueError(f"Data Mover theme failed Hedron 0.60 validation: {_THEME_REPORT.to_dict()}")
_RESOLVED_THEME = DATA_MOVER_THEME_SPEC.to_theme()

# Variants are additive presentation contexts. They do not encode application
# state or behavior; the server and HTMX remain authoritative for that.
DATA_MOVER_THEME: Theme = replace(
    _BRAND_DESIGN.to_theme(),
    tokens=_RESOLVED_THEME.tokens,
    modes=_RESOLVED_THEME.modes,
    accessibility_modes=_RESOLVED_THEME.accessibility_modes,
    variants={
        "workspace": {
            "color.surface": "#101a31",
            "color.surface-muted": "#16223f",
        },
        "auth": {
            "color.surface": "#131e38",
            "color.surface-muted": "#1a2850",
        },
    },
)


# Recipes provide semantic defaults; explicit component props remain authoritative.
DATA_MOVER_DESIGN = DesignSystem.from_theme(DATA_MOVER_THEME).with_recipes(
    StyleRecipe.control(
        "data-mover-primary-action",
        size="md",
        appearance="solid",
        emphasis="primary",
    ),
    StyleRecipe.control(
        "data-mover-secondary-action",
        size="md",
        appearance="outline",
        emphasis="secondary",
    ),
    StyleRecipe.control(
        "data-mover-danger-action",
        size="md",
        appearance="solid",
        emphasis="danger",
    ),
    StyleRecipe.surface(
        "data-mover-panel",
        appearance="raised",
        density="comfortable",
        padding="none",
        elevation="md",
    ),
    StyleRecipe.surface(
        "data-mover-auth-panel",
        appearance="raised",
        density="spacious",
        padding="md",
        elevation="lg",
    ),
    StyleRecipe.surface(
        "data-mover-inset",
        appearance="soft",
        density="comfortable",
        padding="md",
        elevation="none",
    ),
    StyleRecipe.data(
        "data-mover-compact-data",
        density="compact",
        responsive="scroll",
    ),
    StyleRecipe.status(
        "data-mover-operational-status",
        size="sm",
        appearance="soft",
    ),
    StyleRecipe.content(
        "data-mover-supporting-copy",
        role="body",
        overflow="wrap",
    ),
)


def apply_action_recipe(button: _ComponentT, *, variant: str) -> _ComponentT:
    """Apply a named 0.60 control recipe without overriding explicit props."""

    recipe = {
        "primary": "data-mover-primary-action",
        "secondary": "data-mover-secondary-action",
        "danger": "data-mover-danger-action",
    }.get(variant, "data-mover-primary-action")
    return DATA_MOVER_DESIGN.apply(recipe, button)


def surface_card(
    *nodes: Any,
    recipe: str = "data-mover-panel",
    class_: str | None = None,
    **kwargs: Any,
) -> Card:
    """Build a Card with a named Data Mover surface recipe."""

    return DATA_MOVER_DESIGN.apply(
        recipe,
        Card(*nodes, class_=class_, **kwargs),
    )


def apply_data_recipe(
    component: _ComponentT,
    *,
    recipe: str = "data-mover-compact-data",
) -> _ComponentT:
    """Apply a named Hedron data recipe to tables and other data views."""

    return DATA_MOVER_DESIGN.apply(recipe, component)


__all__ = [
    "DATA_MOVER_DESIGN",
    "DATA_MOVER_THEME",
    "DATA_MOVER_THEME_SPEC",
    "DATA_MOVER_FLOW_FAMILY",
    "apply_data_recipe",
    "apply_action_recipe",
    "surface_card",
]
