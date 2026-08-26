"""Trusted Data Mover navigation icons registered with Hedron."""

from __future__ import annotations

from hedron import register_icon

_ICON_SOURCE = "Data Mover navigation icon set"

NAV_ICONS = {
    "pipeline": "data-mover-pipeline",
    "connections": "data-mover-connections",
    "account": "data-mover-account",
    "team": "data-mover-team",
    "activity": "data-mover-activity",
}

_ICON_SVGS = {
    "pipeline": """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="5" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 6h3c3 0 3 6 7 6M7 18h3c3 0 3-6 7-6"/></svg>""",
    "connections": """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9.5 14.5 5-5"/><path d="M7.2 16.8 5.8 18.2a3.5 3.5 0 0 1-5-5l3.4-3.4a3.5 3.5 0 0 1 5 0" transform="translate(2)"/><path d="m14.8 7.2 1.4-1.4a3.5 3.5 0 0 1 5 5l-3.4 3.4a3.5 3.5 0 0 1-5 0"/></svg>""",
    "account": """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="3.25"/><path d="M5.5 20a6.5 6.5 0 0 1 13 0"/></svg>""",
    "team": """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.25"/><path d="M3.5 20a5.5 5.5 0 0 1 11 0M14 15.5a4.5 4.5 0 0 1 6.5 4"/></svg>""",
    "activity": """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12h4l2.2-5 4 10 2.3-5H21"/></svg>""",
}

for _logical_name, _icon_name in NAV_ICONS.items():
    register_icon(
        _icon_name,
        _ICON_SVGS[_logical_name],
        title=_logical_name.title(),
        source=_ICON_SOURCE,
    )

__all__ = ["NAV_ICONS"]
