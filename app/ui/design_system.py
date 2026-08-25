"""Data Mover's explicit Hedron 0.64.1 presentation contract."""

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
    export_theme,
    register_recipe_family,
    validate_theme_spec,
)
from hedron_core import (
    Component,
    ScopedStyleRecipe,
    compile_scoped_styles,
    presentation_contract,
)
from hedron_core.theme import Theme, aurora_theme

_ComponentT = TypeVar("_ComponentT", bound=Component)


# Build a first-party Data Mover brand from Hedron's 0.64.1 typed design system.
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

# 0.64.1's extensible recipe-family contract lets the flow canvas declare its
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

# ThemeBuilder is the canonical authoring layer. The legacy Theme bridge
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
    .tokens(
        {
            "type.display.size": "clamp(2.25rem, 4vw, 4rem)",
            "type.heading.size": "clamp(1.5rem, 3vw, 2.25rem)",
            "type.body.size": "1rem",
            "type.supporting.size": "0.875rem",
            "type.label.size": "0.75rem",
            "type.metadata.size": "0.6875rem",
            "type.body.line-height": "1.55",
            "type.heading.line-height": "0.98",
            "space.1": "0.25rem",
            "space.2": "0.5rem",
            "space.3": "0.75rem",
            "space.4": "1rem",
            "space.5": "1.5rem",
            "space.6": "2rem",
            "geometry.control-height": "2.9rem",
            "geometry.hit-target": "2.75rem",
            "geometry.radius-sm": "0.625rem",
            "geometry.radius-md": "0.875rem",
            "geometry.radius-lg": "1.125rem",
            "geometry.separator": "1px",
            "motion.standard": "160ms",
            "motion.reveal": "220ms",
            "data.row.hover": "rgba(141, 156, 255, 0.08)",
            "data.row.selected": "rgba(111, 112, 255, 0.16)",
            "control.appearance": "auto",
            "control.accent": "var(--hedron-color-accent)",
            # Native Hedron surface bundles consume these short aliases for
            # glass/translucent treatments. Keeping them in the theme lets the
            # component bundle own the effect instead of product CSS.
            "glass.opacity": "78%",
            "glass.blur": "18px",
            "glass.border": "rgb(145 166 204 / 24%)",
            "glass.shadow": "0 18px 54px rgb(0 0 0 / 16%), inset 0 1px rgb(255 255 255 / 4%)",
            "surface.opacity": "78%",
            "surface.blur": "12px",
        }
    )
    .metadata(product="data-mover", release="0.64.1")
    .profile("workflow")
    .build()
)
_THEME_REPORT = validate_theme_spec(DATA_MOVER_THEME_SPEC, profile="workflow")
if not _THEME_REPORT.ok:
    raise ValueError(f"Data Mover theme failed Hedron 0.64.1 validation: {_THEME_REPORT.to_dict()}")
_RESOLVED_THEME = DATA_MOVER_THEME_SPEC.to_theme()

# Variants are additive presentation contexts. They do not encode application
# state or behavior; the server and HTMX remain authoritative for that.
_DATA_MOVER_THEME_TOKENS = {
    **_RESOLVED_THEME.tokens,
    # Let Hedron's native component bundle own the semantic visual language.
    # Product CSS consumes these variables for the remaining art direction.
    "color.bg": "#080d16",
    "color.canvas": "#080d16",
    "color.surface": "#111a2a",
    "color.surface-muted": "#0e1828",
    "color.fg": "#f2f5fb",
    "color.muted": "#9ba9bf",
    "color.border": "rgb(145 166 204 / 19%)",
    "color.accent": "#8d9cff",
    "color.focus": "#8d9cff",
    "color.danger": "#ff8ca6",
    "color.success": "#63e0c6",
    "color.success-soft": "rgb(99 224 198 / 12%)",
    "color.warning": "#f0c76a",
    "color.warning-soft": "rgb(240 199 106 / 13%)",
    "color.info-soft": "rgb(141 156 255 / 12%)",
    "shape.radius": "0.6875rem",
    "shape.radius-lg": "1.125rem",
}

_DATA_MOVER_DARK_MODE = {
    **_RESOLVED_THEME.modes.get("dark", {}),
    "color.bg": "#080d16",
    "color.canvas": "#080d16",
    "color.surface": "#111a2a",
    "color.surface-muted": "#0e1828",
    "color.fg": "#f2f5fb",
    "color.muted": "#9ba9bf",
    "color.border": "rgb(145 166 204 / 19%)",
    "color.accent": "#8d9cff",
    "color.focus": "#8d9cff",
    "color.danger": "#ff8ca6",
    "color.success": "#63e0c6",
    "color.success-soft": "rgb(99 224 198 / 12%)",
    "color.warning": "#f0c76a",
    "color.warning-soft": "rgb(240 199 106 / 13%)",
    "color.info-soft": "rgb(141 156 255 / 12%)",
}

DATA_MOVER_THEME: Theme = replace(
    _BRAND_DESIGN.to_theme(),
    tokens=_DATA_MOVER_THEME_TOKENS,
    modes={**_RESOLVED_THEME.modes, "dark": _DATA_MOVER_DARK_MODE},
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
    elevation={"raised": "0 24px 64px rgb(0 0 0 / 28%)"},
)

# 0.64.1 emits a matching CSS and design-token export, including the compatibility
# bridge consumed by Hedron's default stylesheet. Fail fast if the application
# theme ever drifts outside the published contract.
DATA_MOVER_THEME_EXPORT = export_theme(DATA_MOVER_THEME, profile="workflow")
DATA_MOVER_PRESENTATION = presentation_contract(DATA_MOVER_THEME)

# 0.64.1's scoped-style contract owns the bounded, application-defined chrome
# that can be expressed without private selectors or arbitrary at-rules.
_DATA_MOVER_SCOPED_RECIPES = (
    ScopedStyleRecipe(
        "DataMover",
        "environment-banner",
        {
            "background": "linear-gradient(90deg, rgba(240, 199, 106, 0.13), rgba(240, 199, 106, 0.045))",
            "border": "1px solid rgba(240, 199, 106, 0.22)",
            "border-radius": "0 0 12px 12px",
            "color": "#dfc77f",
            "font-size": "var(--hedron-type-metadata-size)",
            "font-weight": "700",
            "letter-spacing": "0.12em",
            "line-height": "1.4",
            "margin-inline": "auto",
            "max-inline-size": "1360px",
            "padding-block": "0.65rem",
            "padding-inline": "0.95rem",
            "box-shadow": "0 14px 42px rgb(240 199 106 / 8%)",
        },
    ),
    ScopedStyleRecipe(
        "DataMover",
        "shell-nav",
        {
            "background": "linear-gradient(180deg, rgba(18, 28, 47, 0.92), rgba(11, 18, 31, 0.92))",
            "border": "1px solid rgba(145, 166, 204, 0.19)",
            "border-radius": "var(--hedron-geometry-radius-lg)",
            "box-shadow": "0 24px 64px rgb(0 0 0 / 28%), inset 0 1px rgba(255, 255, 255, 0.045)",
            "margin-block": "2.4rem",
            "margin-inline": "2.5rem 0",
        },
    ),
    ScopedStyleRecipe(
        "DataMover",
        "brand",
        {
            "column-gap": "0.8rem",
            "color": "var(--hedron-color-fg)",
        },
    ),
    ScopedStyleRecipe(
        "DataMover",
        "page-header",
        {"margin-block": "0 1.85rem"},
    ),
    ScopedStyleRecipe(
        "DataMover",
        "process-flow",
        {
            "background": "linear-gradient(135deg, rgba(13, 21, 38, 0.88), rgba(8, 14, 25, 0.72))",
            "border": "1px solid rgba(145, 166, 204, 0.24)",
            "border-radius": "var(--hedron-geometry-radius-md)",
            "box-shadow": "0 18px 48px rgb(0 0 0 / 12%), inset 0 1px rgb(255 255 255 / 4%)",
            "padding-block": "0.35rem",
            "padding-inline": "0.35rem",
        },
    ),
)
DATA_MOVER_SCOPED_STYLES = compile_scoped_styles(_DATA_MOVER_SCOPED_RECIPES)
DATA_MOVER_SCOPE_CLASSES = {recipe.part: recipe.class_name for recipe in _DATA_MOVER_SCOPED_RECIPES}


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
    """Apply a named 0.64.1 control recipe without overriding explicit props."""

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

    class_name = class_ or "hedron-card--glass"
    return DATA_MOVER_DESIGN.apply(
        recipe,
        Card(*nodes, class_=class_name, **kwargs),
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
    "DATA_MOVER_THEME_EXPORT",
    "DATA_MOVER_PRESENTATION",
    "DATA_MOVER_SCOPED_STYLES",
    "DATA_MOVER_SCOPE_CLASSES",
    "apply_data_recipe",
    "apply_action_recipe",
    "surface_card",
]
