"""Data Mover composition recipes using Hedron's unmodified Folio theme."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, TypeVar

from hedron import (
    Card,
    Component,
    DesignSystem,
    PageHeader,
    Stack,
    StyleRecipe,
    Surface,
    Theme,
    ThemeBuilder,
    export_theme,
)
from hedron_core import presentation_contract
from hedron_core.theme import folio_theme

_ComponentT = TypeVar("_ComponentT", bound=Component)


class DataMoverPageHeader(PageHeader):
    """Readable page headings configured entirely through native typography props."""

    def __init__(self, title: str, **kwargs: Any) -> None:
        kwargs.setdefault("title_measure", "wide")
        kwargs.setdefault("description_measure", "default")
        kwargs.setdefault("title_effect", "none")
        kwargs.setdefault("description_effect", "none")
        kwargs.setdefault("title_tracking", "tight")
        kwargs.setdefault("title_wrap", "balance")
        kwargs.setdefault("description_wrap", "pretty")
        kwargs.setdefault("eyebrow_tone", "accent")
        kwargs.setdefault("eyebrow_tracking", "wide")
        super().__init__(title, **kwargs)


# The name identifies this recipe catalog; all palette, typography, geometry,
# accessibility modes, and component state styling come directly from Folio.
DATA_MOVER_THEME: Theme = replace(folio_theme(), name="data-mover")
DATA_MOVER_THEME_SPEC = ThemeBuilder.from_theme(DATA_MOVER_THEME).profile("workflow").build()
DATA_MOVER_THEME_EXPORT = export_theme(DATA_MOVER_THEME, profile="workflow")
DATA_MOVER_PRESENTATION = presentation_contract(DATA_MOVER_THEME)

# Recipes select supported component props. They generate no app-authored CSS.
# Explicit props at the call site remain authoritative.
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
        appearance="solid",
        density="comfortable",
        padding="md",
        elevation="none",
        responsive={"padding": {"xl": "lg"}},
    ),
    StyleRecipe.surface(
        "data-mover-auth-panel",
        appearance="raised",
        density="comfortable",
        padding="lg",
        elevation="sm",
    ),
    StyleRecipe.surface(
        "data-mover-inset",
        appearance="raised",
        density="comfortable",
        padding="md",
        elevation="none",
    ),
    StyleRecipe.data("data-mover-compact-data", density="compact"),
    StyleRecipe.status("data-mover-operational-status", size="sm", appearance="soft"),
    StyleRecipe.content(
        "data-mover-supporting-copy",
        role="body",
        overflow="wrap",
        measure="default",
        effect="none",
    ),
    StyleRecipe.content("data-mover-page-title", role="title", measure="wide", effect="none"),
    StyleRecipe.content("data-mover-page-copy", role="body", measure="default", effect="none"),
    StyleRecipe.content("data-mover-auth-title", role="title", measure="wide", effect="none"),
    StyleRecipe.content("data-mover-auth-copy", role="body", measure="default", effect="none"),
)


def apply_action_recipe(button: _ComponentT, *, variant: str) -> _ComponentT:
    """Select a native control appearance without overriding explicit props."""

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
    """Build a Card using supported Hedron surface props."""

    return DATA_MOVER_DESIGN.apply(recipe, Card(*nodes, class_=class_, **kwargs))


def stacked_surface(*nodes: Any, gap: str = "md", **kwargs: Any) -> Surface:
    """Compose a native surface with explicit spacing between its children."""

    return Surface(Stack(*nodes, gap=gap), **kwargs)


def apply_data_recipe(
    component: _ComponentT,
    *,
    recipe: str = "data-mover-compact-data",
) -> _ComponentT:
    """Apply a native data density to tables and other data views."""

    return DATA_MOVER_DESIGN.apply(recipe, component)


__all__ = [
    "DATA_MOVER_DESIGN",
    "DataMoverPageHeader",
    "DATA_MOVER_THEME",
    "DATA_MOVER_THEME_SPEC",
    "DATA_MOVER_THEME_EXPORT",
    "DATA_MOVER_PRESENTATION",
    "apply_data_recipe",
    "apply_action_recipe",
    "surface_card",
    "stacked_surface",
]
