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

TRANSFER_ICONS = {
    "csv": "data-mover-file-table",
    "postgres": "data-mover-database",
    "databricks": "data-mover-layers",
    "mss": "data-mover-layers",
    "mcscop": "data-mover-layers",
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

for _name, _paths in {
    "file-table": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h8M11 13v4"/>',
    "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    "layers": '<path d="m12 3 10 5-10 5L2 8Z"/><path d="m2 12 10 5 10-5M2 16l10 5 10-5"/>',
    "transfer-arrow": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "transfer-check": '<path d="m5 12 4 4L19 6"/>',
    "transfer-pause": '<path d="M9 5v14M15 5v14"/>',
    "transfer-attention": '<path d="M12 7v6M12 17h.01"/><path d="m12 3 10 18H2Z"/>',
}.items():
    register_icon(
        f"data-mover-{_name}",
        f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_paths}</svg>',
        title=_name.replace("-", " ").title(),
        source="Data Mover transfer icon set",
    )

__all__ = ["NAV_ICONS", "TRANSFER_ICONS"]
