"""Data Mover's explicit Hedron 0.58 presentation contract."""

from __future__ import annotations

from typing import TypeVar

from hedron import DesignSystem, StyleRecipe
from hedron_core import Component
from hedron_core.theme import aurora_theme

_ComponentT = TypeVar("_ComponentT", bound=Component)


# Keep Aurora's established visual identity while making presentation intent
# inspectable through Hedron's 0.58 design-system plan. Recipes only provide
# defaults: explicit button props and the application's semantic variant remain
# authoritative.
DATA_MOVER_DESIGN = DesignSystem.from_theme(aurora_theme()).with_recipes(
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
)


def apply_action_recipe(button: _ComponentT, *, variant: str) -> _ComponentT:
    """Apply a named 0.58 control recipe without overriding explicit props."""

    recipe = {
        "primary": "data-mover-primary-action",
        "secondary": "data-mover-secondary-action",
        "danger": "data-mover-danger-action",
    }.get(variant, "data-mover-primary-action")
    return DATA_MOVER_DESIGN.apply(recipe, button)


__all__ = ["DATA_MOVER_DESIGN", "apply_action_recipe"]
