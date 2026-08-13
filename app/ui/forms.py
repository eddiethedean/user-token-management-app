"""Application-themed form helpers built on Hedron primitives."""

from __future__ import annotations

from typing import Any

from hedron import CsrfField, html
from hedron_core import NodeLike


def hidden_field(name: str, value: str) -> NodeLike:
    return html.input(type="hidden", name=name, value=value)


def csrf_hidden(token: str, *, name: str = "csrf_token") -> NodeLike:
    return CsrfField(name=name, token=token)


def submit_button(
    label: str,
    *,
    variant: str = "primary",
    wide: bool = False,
    small: bool = False,
    quiet: bool = False,
    danger: bool = False,
    type: str = "submit",
    **attrs: Any,
) -> NodeLike:
    """Render a themed button (Data Mover ``.button`` classes)."""
    classes = ["button"]
    if quiet:
        classes.append("button-quiet")
    elif danger:
        classes.append("button-danger")
    elif variant == "secondary":
        classes.append("button-secondary")
    else:
        classes.append("button-primary")
    if wide:
        classes.append("button-wide")
    if small:
        classes.append("button-small")
    existing = attrs.pop("class_", None)
    if existing:
        classes.append(str(existing))
    return html.button(label, class_=" ".join(classes), type=type, **attrs)
