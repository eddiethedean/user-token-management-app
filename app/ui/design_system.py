"""Data Mover's explicit Hedron 0.66.1 presentation contract."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, TypeVar

from hedron import (
    Card,
    Color,
    DesignSystem,
    PageHeader,
    RecipeFamily,
    StyleRecipe,
    ThemeBuilder,
    export_theme,
    register_recipe_family,
    validate_theme_spec,
)
from hedron_core import (
    Component,
    ResponsiveCondition,
    ScopedStyleRecipe,
    compile_scoped_styles,
    motion_recipes,
    presentation_contract,
)
from hedron_core.theme import Theme, aurora_theme

_ComponentT = TypeVar("_ComponentT", bound=Component)


class DataMoverPageHeader(PageHeader):
    """PageHeader with product typography expressed through native Hedron props."""

    def __init__(self, title: str, **kwargs: Any) -> None:
        kwargs.setdefault("title_measure", "narrow")
        kwargs.setdefault("description_measure", "default")
        kwargs.setdefault("title_effect", "display")
        kwargs.setdefault("description_effect", "subtle")
        super().__init__(title, **kwargs)


# Build a first-party Data Mover brand from Hedron's 0.66.1 typed design system.
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

# 0.66.1's extensible recipe-family contract lets the flow canvas declare its
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

# Hedron 0.65's named motion catalog and scoped public hooks keep product
# interaction treatments reviewable, bounded, and accessible.
DATA_MOVER_MOTION_RECIPES = motion_recipes()
DATA_MOVER_SCOPED_STYLE_RECIPES = (
    ScopedStyleRecipe(
        component="ProcessFlow",
        part="step",
        states=("current",),
        conditions=(ResponsiveCondition.viewport_range("md", "xl"),),
        declarations={
            "background-color": "var(--hedron-color-accent-soft)",
            "border-color": "var(--hedron-color-accent)",
            "box-shadow": "var(--hedron-elevation-focus)",
        },
        motion="elevate",
    ),
    ScopedStyleRecipe(
        component="AppShell",
        part="nav.link",
        states=("current",),
        declarations={
            "background-color": "var(--hedron-color-accent-soft)",
            "box-shadow": "var(--hedron-elevation-focus)",
            "color": "var(--hedron-color-accent)",
            "font-weight": "750",
            "letter-spacing": "0.01em",
        },
        motion="elevate",
    ),
)
DATA_MOVER_SCOPED_STYLES = compile_scoped_styles(DATA_MOVER_SCOPED_STYLE_RECIPES)
PROCESS_FLOW_STEP_STYLE_CLASS = next(
    recipe.class_name
    for recipe in DATA_MOVER_SCOPED_STYLE_RECIPES
    if recipe.component == "ProcessFlow"
)
APP_SHELL_NAV_STYLE_CLASS = next(
    recipe.class_name
    for recipe in DATA_MOVER_SCOPED_STYLE_RECIPES
    if recipe.component == "AppShell"
)

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
            "type.measure.narrow": "18ch",
            "type.effect.display": "0 12px 36px rgb(0 0 0 / 24%)",
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
            "motion.instant": "0ms",
            "motion.standard": "160ms",
            "motion.emphasized": "300ms",
            "motion.reveal": "220ms",
            "motion.elevate": "180ms",
            "motion.crossfade": "200ms",
            "motion.easing.standard": "cubic-bezier(0.2, 0, 0, 1)",
            "ambient.opacity.soft": "0.88",
            "ambient.opacity.subtle": "0.42",
            "data.row.hover": "rgba(141, 156, 255, 0.08)",
            "data.row.selected": "rgba(111, 112, 255, 0.16)",
            "control.appearance": "auto",
            "control.accent": "var(--hedron-color-accent)",
            # Hedron 0.65's native surface, table, control, and motion bundles
            # consume these semantic tokens directly; product CSS does not
            # need to restyle those component states.
            "surface.translucent.opacity": "78%",
            "surface.translucent.blur": "12px",
            "surface.glass.opacity": "78%",
            "surface.glass.blur": "18px",
            "surface.glass.border": "rgb(145 166 204 / 24%)",
            "surface.glass.shadow": "0 24px 72px rgb(2 8 23 / 18%), inset 0 1px rgb(255 255 255 / 7%)",
            # The component-scoped bundle uses the shorter aliases while the
            # complete Hedron stylesheet uses the surface.* names above.
            "glass.opacity": "78%",
            "glass.blur": "18px",
            "glass.border": "rgb(145 166 204 / 24%)",
            "glass.shadow": "0 24px 72px rgb(2 8 23 / 18%), inset 0 1px rgb(255 255 255 / 7%)",
            "data.table.border": "var(--hedron-color-border)",
            "data.table.radius": "var(--hedron-geometry-radius-md)",
            "data.table.header.background": "var(--hedron-color-surface-muted)",
            "data.table.header.foreground": "var(--hedron-color-fg)",
            "data.table.header.weight": "700",
            "data.table.header.tracking": "0.04em",
            "data.table.row.separator": "var(--hedron-color-border)",
            "data.table.numeric": "tabular-nums",
            "data.table.code": "ui-monospace, SFMono-Regular, Menlo, monospace",
            "data.table.sticky.surface": "var(--hedron-color-surface)",
            "data.table.sticky.elevation": "var(--hedron-elevation-raised)",
            "data.table.density": "0.9",
            "control.focus": "var(--hedron-color-focus)",
            "control.invalid": "var(--hedron-color-danger)",
            "control.busy": "var(--hedron-color-muted)",
            "control.disabled": "var(--hedron-color-muted)",
            "control.read-only": "var(--hedron-color-muted)",
            "control.checked": "var(--hedron-color-accent)",
            "control.selected": "var(--hedron-color-accent)",
            "control.indeterminate": "var(--hedron-color-accent)",
        }
    )
    .metadata(product="data-mover", release="0.66.1")
    .profile("workflow")
    .build()
)
_THEME_REPORT = validate_theme_spec(DATA_MOVER_THEME_SPEC, profile="workflow")
if not _THEME_REPORT.ok:
    raise ValueError(f"Data Mover theme failed Hedron 0.66.1 validation: {_THEME_REPORT.to_dict()}")
_RESOLVED_THEME = DATA_MOVER_THEME_SPEC.to_theme()

# Variants are additive presentation contexts. They do not encode application
# state or behavior; the server and HTMX remain authoritative for that.
_DATA_MOVER_THEME_TOKENS = {
    **_RESOLVED_THEME.tokens,
    # Let Hedron's native component bundle own the semantic visual language.
    # The base palette is the explicit light mode. Hedron's more-specific dark
    # mode below applies for dark and system-dark rendering, including nested
    # StyleScope elements.
    "color.bg": "#f6f6fb",
    "color.canvas": "#f6f6fb",
    "color.surface": "#ffffff",
    "color.surface-muted": "#ebecf7",
    "color.fg": "#23275c",
    "color.muted": "#676cad",
    "color.border": "#cfd1e8",
    "color.accent": "#1a2aff",
    "color.focus": "#1a2aff",
    "color.danger": "#d04348",
    "color.success": "#18745a",
    "color.success-soft": "#e7f7f1",
    "color.warning": "#8a5a00",
    "color.warning-soft": "#fff3d1",
    "color.info-soft": "#e8ecff",
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
            "color.surface": "#ffffff",
            "color.surface-muted": "#f0f1f9",
        },
        "auth": {
            "color.surface": "#ffffff",
            "color.surface-muted": "#f0f1fb",
            "type.measure.narrow": "12ch",
            "type.effect.display": "0 18px 44px rgb(0 0 0 / 28%)",
        },
    },
    elevation={
        "focus": "0 12px 32px rgb(2 8 23 / 14%)",
        "raised": "0 1px 0 rgb(255 255 255 / 6%), 0 28px 84px rgb(2 8 23 / 24%)",
    },
)

# 0.66.1 emits a matching CSS and design-token export, including the compatibility
# bridge consumed by Hedron's default stylesheet. Fail fast if the application
# theme ever drifts outside the published contract.
DATA_MOVER_THEME_EXPORT = export_theme(DATA_MOVER_THEME, profile="workflow")
DATA_MOVER_PRESENTATION = presentation_contract(DATA_MOVER_THEME)

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
        measure="default",
        effect="subtle",
    ),
    StyleRecipe.content(
        "data-mover-page-title",
        role="title",
        measure="narrow",
        effect="display",
    ),
    StyleRecipe.content(
        "data-mover-page-copy",
        role="body",
        measure="default",
        effect="subtle",
    ),
    StyleRecipe.content(
        "data-mover-auth-title",
        role="title",
        measure="narrow",
        effect="display",
    ),
    StyleRecipe.content(
        "data-mover-auth-copy",
        role="body",
        measure="default",
        effect="subtle",
    ),
)


def apply_action_recipe(button: _ComponentT, *, variant: str) -> _ComponentT:
    """Apply a named 0.66.1 control recipe without overriding explicit props."""

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
    "DataMoverPageHeader",
    "DATA_MOVER_THEME",
    "DATA_MOVER_THEME_SPEC",
    "DATA_MOVER_FLOW_FAMILY",
    "DATA_MOVER_MOTION_RECIPES",
    "DATA_MOVER_SCOPED_STYLE_RECIPES",
    "DATA_MOVER_SCOPED_STYLES",
    "APP_SHELL_NAV_STYLE_CLASS",
    "PROCESS_FLOW_STEP_STYLE_CLASS",
    "DATA_MOVER_THEME_EXPORT",
    "DATA_MOVER_PRESENTATION",
    "apply_data_recipe",
    "apply_action_recipe",
    "surface_card",
]
