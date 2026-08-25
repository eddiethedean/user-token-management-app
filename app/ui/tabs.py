"""Native Hedron navigation tabs with the app's shared desktop defaults."""

from __future__ import annotations

from typing import Any

from hedron import Tabs
from hedron_core import NodeLike


class NavigationTabs(Tabs):
    """Use Hedron 0.63's first-class underline, density, and overflow styling."""

    def __init__(
        self,
        *items: tuple[str, NodeLike] | list[tuple[str, NodeLike]],
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("appearance", "underline")
        kwargs.setdefault("density", "compact")
        kwargs.setdefault("responsive", "scroll")
        super().__init__(*items, **kwargs)


__all__ = ["NavigationTabs"]
