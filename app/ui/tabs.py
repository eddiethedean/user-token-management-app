"""Navigation-style tabs composed from Hedron's native presentation markers."""

from __future__ import annotations

from hedron import Tabs, html
from hedron_core import NodeLike


class NavigationTabs(Tabs):
    """Keep Hedron's tab semantics while using its plain control appearance.

    Hedron 0.60 renders ``Tabs`` with the secondary-button treatment and does
    not yet expose an appearance prop. This small presentation adapter keeps
    the framework's IDs, ARIA relationships, and browser behavior while using
    Hedron's public plain-control markers instead of application CSS.
    """

    def render(self) -> NodeLike:
        if not self._panels:
            return html.div(
                id=self.props.id,
                class_=self._root_class(),
                data={"hedron-navigation-tabs": "true"},
            )

        tabs_id = self.props.id or f"tabs-{self.render_instance_id()}"
        active = self.props.active or self._panels[0][0]
        tablist: list[NodeLike] = []
        panels: list[NodeLike] = []

        for index, (name, content) in enumerate(self._panels):
            tab_id = f"{tabs_id}-tab-{index}"
            panel_id = f"{tabs_id}-panel-{index}"
            selected = name == active
            label: NodeLike = html.u(html.strong(name)) if selected else name
            tablist.append(
                html.button(
                    label,
                    type="button",
                    role="tab",
                    id=tab_id,
                    class_="hedron-button hedron-button-primary",
                    aria={
                        "selected": "true" if selected else "false",
                        "controls": panel_id,
                    },
                    data={
                        "hedron-appearance": "plain",
                        "hedron-emphasis": "primary" if selected else "neutral",
                        "hedron-size": "sm",
                        "navigation-tab": "true",
                        "navigation-tab-label": name,
                    },
                    tabindex="0" if selected else "-1",
                )
            )
            panels.append(
                html.div(
                    content,
                    role="tabpanel",
                    id=panel_id,
                    aria={"labelledby": tab_id},
                    hidden=None if selected else True,
                )
            )

        return html.div(
            html.div(*tablist, role="tablist", class_="hedron-tablist"),
            *panels,
            id=tabs_id,
            class_=self._root_class(),
            data={"hedron-navigation-tabs": "true"},
        )

    def _root_class(self) -> str:
        return " ".join(part for part in ("hedron-tabs", self.props.class_) if part)


__all__ = ["NavigationTabs"]
