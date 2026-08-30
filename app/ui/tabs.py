"""Native Hedron navigation tabs with the app's shared desktop defaults."""

from __future__ import annotations

from typing import Any

from hedron import Tabs
from hedron_core import NodeLike


class NavigationTabs(Tabs):
    """Use Hedron 1.0.0's first-class underline and density styling."""

    def __init__(
        self,
        *items: tuple[str, NodeLike] | list[tuple[str, NodeLike]],
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("appearance", "underline")
        kwargs.setdefault("density", "compact")
        super().__init__(*items, **kwargs)


__all__ = ["NavigationTabs"]
