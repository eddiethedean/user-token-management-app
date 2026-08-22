"""Data Mover's explicit Hedron 0.58 presentation contract."""

from __future__ import annotations

from typing import Any, TypeVar

from hedron import Card, DesignSystem, StyleRecipe
from hedron_core import Component
from hedron_core.theme import aurora_theme

_ComponentT = TypeVar("_ComponentT", bound=Component)


# Aurora is Hedron's bundled dark-compatible theme. Keep it as the registered
# base until the app ships a compiled theme asset for a derived theme name.
DATA_MOVER_THEME = aurora_theme()


# Keep Aurora's established visual identity while making presentation intent
# inspectable through Hedron's 0.58 design-system plan. Recipes only provide
# defaults: explicit component props remain authoritative.
DATA_MOVER_DESIGN = DesignSystem.from_theme(DATA_MOVER_THEME).with_recipes(
    StyleRecipe.control(
        "data-mover-primary-action",
        appearance="solid",
        emphasis="primary",
    ),
    StyleRecipe.control(
        "data-mover-secondary-action",
        appearance="outline",
        emphasis="secondary",
    ),
    StyleRecipe.control(
        "data-mover-danger-action",
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
)


def apply_action_recipe(button: _ComponentT, *, variant: str) -> _ComponentT:
    """Apply a named 0.58 control recipe without overriding explicit props."""

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
    "apply_data_recipe",
    "apply_action_recipe",
    "surface_card",
]
