"""Application-themed form helpers built on Hedron primitives."""

from __future__ import annotations

from typing import Any, Literal, cast

from hedron import Button, CsrfField, html
from hedron_core import Component, HtmlAttrValue, NodeLike, Props

from app.ui.design_system import apply_action_recipe


def hidden_field(name: str, value: str) -> NodeLike:
    return html.input(type="hidden", name=name, value=value)


def csrf_hidden(token: str, *, name: str = "csrf_token") -> NodeLike:
    return CsrfField(name=name, token=token)


class CompactPasswordInputProps(Props):
    """Props required for FormField to bind a compact password control."""

    name: str
    id: str | None = None
    value: str = ""
    placeholder: str | None = None
    required: bool = False
    autocomplete: str | None = None
    aria_describedby: str | None = None
    aria_invalid: str | None = None
    aria_required: str | None = None


class CompactPasswordInput(Component[CompactPasswordInputProps]):
    """Hedron password input with a quiet native Button reveal action."""

    props_type = CompactPasswordInputProps

    def __init__(
        self,
        name: str,
        *,
        id: str,
        autocomplete: str | None = None,
        required: bool = False,
        placeholder: str | None = None,
        value: str = "",
        aria_describedby: str | None = None,
        aria_invalid: str | None = None,
        aria_required: str | None = None,
    ) -> None:
        super().__init__(
            CompactPasswordInputProps(
                name=name,
                id=id,
                autocomplete=autocomplete,
                required=required,
                placeholder=placeholder,
                value=value,
                aria_describedby=aria_describedby,
                aria_invalid=aria_invalid,
                aria_required=aria_required,
            )
        )

    def render(self) -> NodeLike:
        field_id = self.props.id or f"field-{self.props.name}"
        input_attrs: dict[str, HtmlAttrValue] = {
            "id": field_id,
            "name": self.props.name,
            "type": "password",
            "value": self.props.value,
            "required": self.props.required or None,
            "autocomplete": self.props.autocomplete,
            "placeholder": self.props.placeholder,
            "class_": "hedron-text-input",
            "aria": {
                "describedby": self.props.aria_describedby,
                "invalid": self.props.aria_invalid,
                "required": self.props.aria_required,
            },
        }
        return html.span(
            html.input(**input_attrs),
            apply_action_recipe(
                Button(
                    "Show",
                    type="button",
                    variant="secondary",
                    size="sm",
                    id=f"{field_id}-visibility",
                    attrs={
                        "data-hedron-password-toggle": field_id,
                        "data-compact-password-toggle": "true",
                        "aria-controls": field_id,
                        "aria-label": "Show password",
                        "aria-pressed": "false",
                    },
                ),
                variant="secondary",
            ),
            class_="hedron-password-field",
            data={"hedron-password": "true"},
        )


def compact_password_input(
    name: str,
    *,
    id: str,
    autocomplete: str | None = None,
    required: bool = False,
    placeholder: str | None = None,
    value: str = "",
) -> NodeLike:
    """Render a roomy password field with a quiet native Hedron reveal action."""

    return CompactPasswordInput(
        name,
        id=id,
        autocomplete=autocomplete,
        required=required,
        placeholder=placeholder,
        value=value,
    )


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
