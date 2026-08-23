"""Application-themed form helpers built on Hedron primitives."""

from __future__ import annotations

from typing import Any, Literal, cast

from hedron import Button, CsrfField, html
from hedron_core import NodeLike

from app.ui.design_system import apply_action_recipe


def hidden_field(name: str, value: str) -> NodeLike:
    return html.input(type="hidden", name=name, value=value)


def csrf_hidden(token: str, *, name: str = "csrf_token") -> NodeLike:
    return CsrfField(name=name, token=token)


def submit_button(
    label: str,
    *,
    variant: str = "primary",
    size: Literal["sm", "md", "lg"] = "md",
    width: Literal["content", "field", "full"] | None = None,
    quiet: bool = False,
    danger: bool = False,
    type: str = "submit",
    **attrs: Any,
) -> NodeLike:
    """Render a typed Hedron button with semantic size and width contracts."""
    classes: list[str] = []
    if quiet:
        native_variant = "secondary"
    elif danger:
        native_variant = "danger"
    elif variant == "secondary":
        native_variant = "secondary"
    else:
        native_variant = "primary"
    existing = attrs.pop("class_", None)
    if existing:
        classes.append(str(existing))
    button = Button(
        label,
        class_=" ".join(classes) or None,
        type=cast(Literal["button", "submit", "reset"], type),
        variant=cast(Literal["primary", "secondary", "danger"], native_variant),
        size=size,
        width=width,
        **attrs,
    )
    return apply_action_recipe(button, variant=native_variant)
